"""
Live venue execution — submit, confirm, monitor, resolve.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional

from trading_ai.governance.storage_architecture import append_shark_audit_record
from trading_ai.shark.models import (
    ConfirmationResult,
    ExecutionIntent,
    OpenPosition,
    OrderResult,
)
from trading_ai.shark.risk_context import check_drawdown_after_resolution
from trading_ai.shark.state_store import load_positions, save_positions

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]


def ezras_dry_run_from_env() -> bool:
    """True when ``EZRAS_DRY_RUN`` is set to a truthy value (1, true, yes). Default: false → live execution."""
    v = (os.environ.get("EZRAS_DRY_RUN") or "").strip().lower()
    return v in ("1", "true", "yes")


def manifold_real_money_execution_enabled() -> bool:
    """Manifold is play-money (mana) unless ``MANIFOLD_REAL_MONEY`` is exactly ``true``."""
    return (os.environ.get("MANIFOLD_REAL_MONEY") or "").strip().lower() == "true"


def submit_order(intent: ExecutionIntent) -> OrderResult:
    """
    Live submit — credentials:
    Kalshi: ``KALSHI_API_KEY`` (``KalshiClient.place_order``).
    Polymarket: execution blocked by default (US geoblock / ``POLY_EXECUTION_ENABLED``); scanning still uses Polymarket data.
    Manifold: ``MANIFOLD_API_KEY`` (``submit_manifold_bet``).
    """
    o = (intent.outlet or "").lower()
    if o == "polymarket":
        poly_exec = (os.getenv("POLY_EXECUTION_ENABLED") or "false").strip().lower()
        if poly_exec != "true":
            logger.warning("Polymarket execution disabled — intelligence-only (POLY_EXECUTION_ENABLED not true)")
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="polymarket",
                raw={},
                success=False,
                reason="Polymarket execution disabled",
            )
        logger.warning("Polymarket order blocked — US geoblock (scan-only intelligence)")
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="geo_blocked",
            outlet="polymarket",
            raw={},
            success=False,
            reason="US geoblock — scan only",
        )
    if o == "kalshi":
        from trading_ai.shark.outlets.kalshi import KalshiClient, fetch_kalshi_orderbook_best_ask_cents

        client = KalshiClient()
        ticker = intent.market_id
        if ":" in ticker:
            ticker = ticker.split(":")[-1]
        base_yes_cents = (
            int(round(intent.expected_price * 100))
            if intent.side == "yes"
            else int(round((1.0 - intent.expected_price) * 100))
        )
        base_yes_cents = max(1, min(99, base_yes_cents))
        meta = intent.meta or {}
        is_hv = bool(meta.get("near_resolution_hv"))

        if not is_hv:
            return client.place_order(
                ticker=ticker,
                side=intent.side,
                count=max(1, int(intent.shares)),
                yes_price_cents=base_yes_cents,
            )

        hv_mode = (os.environ.get("KALSHI_HV_ORDER_MODE") or "market").strip().lower()
        try:
            bump = int((os.environ.get("KALSHI_HV_LIMIT_BUMP_CENTS") or "2").strip() or "2")
        except ValueError:
            bump = 2
        bump = max(0, min(5, bump))

        if hv_mode == "market":
            logger.info(
                "Kalshi HV submit: mode=market ticker=%s side=%s count=%s (snapshot limit would be yes_price=%s¢)",
                ticker,
                intent.side,
                max(1, int(intent.shares)),
                base_yes_cents,
            )
            return client.place_order(
                ticker=ticker,
                side=intent.side,
                count=max(1, int(intent.shares)),
                order_type="market",
            )

        ask_yes, ask_no = fetch_kalshi_orderbook_best_ask_cents(ticker, client=client)
        limit_yes: int = base_yes_cents
        if intent.side == "yes" and ask_yes is not None:
            limit_yes = min(99, max(1, ask_yes + bump))
        elif intent.side == "no" and ask_no is not None:
            aggressive_no = min(99, max(1, ask_no + bump))
            limit_yes = min(99, max(1, 100 - aggressive_no))
        else:
            limit_yes = min(99, max(1, base_yes_cents + bump))

        tif = (os.environ.get("KALSHI_HV_TIME_IN_FORCE") or "").strip() or None
        logger.info(
            "Kalshi HV submit: aggressive_limit ticker=%s side=%s ask_yes=%s ask_no=%s base_yes_price=%s¢ limit_yes_price=%s¢ bump=%s",
            ticker,
            intent.side,
            ask_yes,
            ask_no,
            base_yes_cents,
            limit_yes,
            bump,
        )
        return client.place_order(
            ticker=ticker,
            side=intent.side,
            count=max(1, int(intent.shares)),
            yes_price_cents=limit_yes,
            time_in_force=tif,
        )
    if o == "manifold":
        if not manifold_real_money_execution_enabled():
            logger.info("Manifold skipped — play money only")
            raise ValueError("manifold_play_money_skip")
        from trading_ai.shark.manifold_live import submit_manifold_bet

        return submit_manifold_bet(intent)
    if o == "metaculus":
        logger.info("Metaculus is intelligence-only — no orders")
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="intelligence_only",
            outlet="metaculus",
            raw={},
            success=False,
            reason="Metaculus has no tradeable venue in this stack",
        )
    if o == "coinbase":
        if (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip().lower() != "true":
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="coinbase",
                raw={},
                success=False,
                reason="COINBASE_EXECUTION_ENABLED is not true",
            )
        from trading_ai.shark.outlets.coinbase import CoinbaseFetcher

        pid = str(intent.meta.get("product_id") or intent.market_id or "BTC-USD")
        side = str(intent.side or "buy")
        size = str(intent.meta.get("base_size") or intent.shares or "0")
        r = CoinbaseFetcher.place_market_order(pid, side, size)
        return OrderResult(
            order_id=str((r.get("raw") or {}).get("order_id", "") or ""),
            filled_price=float(intent.expected_price or 0.0),
            filled_size=float(intent.shares or 0.0),
            timestamp=time.time(),
            status="submitted" if r.get("ok") else "error",
            outlet="coinbase",
            raw=r,
            success=bool(r.get("ok")),
            reason=None if r.get("ok") else str(r.get("error")),
        )
    if o == "robinhood":
        if (os.environ.get("ROBINHOOD_EXECUTION_ENABLED") or "").strip().lower() != "true":
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="robinhood",
                raw={},
                success=False,
                reason="ROBINHOOD_EXECUTION_ENABLED is not true",
            )
        from trading_ai.shark.outlets.robinhood import RobinhoodFetcher

        sym = str(intent.market_id or intent.meta.get("symbol") or "").upper()
        sh = float(intent.shares or intent.meta.get("shares") or 0.0)
        rh = RobinhoodFetcher()
        if (intent.side or "buy").lower() == "sell":
            r = rh.sell_market(sym, sh)
        else:
            r = rh.buy_market(sym, sh)
        return OrderResult(
            order_id=str((r.get("raw") or {}).get("id", "") or ""),
            filled_price=float(intent.expected_price or 0.0),
            filled_size=sh,
            timestamp=time.time(),
            status="submitted" if r.get("ok") else "error",
            outlet="robinhood",
            raw=r,
            success=bool(r.get("ok")),
            reason=None if r.get("ok") else str(r.get("error")),
        )
    if o == "tastytrade":
        if (os.environ.get("TASTYTRADE_EXECUTION_ENABLED") or "").strip().lower() != "true":
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="tastytrade",
                raw={},
                success=False,
                reason="TASTYTRADE_EXECUTION_ENABLED is not true",
            )
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="not_wired",
            outlet="tastytrade",
            raw={},
            success=False,
            reason="Map options legs via TastytradeClient.place_order before enabling",
        )
    raise ValueError(f"unknown outlet: {intent.outlet}")


def confirm_execution(
    order_result: OrderResult,
    intent: ExecutionIntent,
    *,
    sleep_fn: Optional[SleepFn] = None,
    time_fn: Optional[Callable[[], float]] = None,
    poll_order: Optional[Callable[[str], Dict[str, Any]]] = None,
    cancel_order: Optional[Callable[[str], None]] = None,
) -> ConfirmationResult:
    """Verify fill; slippage vs expected_price; optional unfilled cancel after 60s."""
    sleep = sleep_fn or time.sleep
    now = time_fn or time.time
    exp = max(intent.expected_price, 1e-9)
    fp = order_result.filled_price or exp
    slip = abs(fp - exp) / exp
    edge = max(intent.edge_after_fees, 1e-9)
    high_slip = slip > (0.30 * edge)

    status_l = (order_result.status or "").lower()
    unfilled_cancelled = False
    if status_l in ("resting", "open", "pending") and intent.outlet.lower() == "kalshi":
        t0 = now()
        sleep(30)
        po = poll_order or _default_poll_kalshi
        st = po(order_result.order_id)
        st_s = str(st.get("status") or st.get("order", {}).get("status") or "").lower()
        if st_s in ("resting", "open", "pending"):
            while now() - t0 < 60:
                sleep(2)
                st = po(order_result.order_id)
                st_s = str(st.get("status") or "").lower()
                if st_s in ("filled", "executed", "closed"):
                    break
            else:
                co = cancel_order or _default_cancel_kalshi
                try:
                    co(order_result.order_id)
                except Exception as exc:
                    logger.warning("cancel failed: %s", exc)
                unfilled_cancelled = True
                return ConfirmationResult(
                    actual_fill_price=fp,
                    actual_fill_size=0.0,
                    slippage_pct=slip,
                    confirmed=False,
                    high_slippage_warning=high_slip,
                    unfilled_cancelled=True,
                )

    return ConfirmationResult(
        actual_fill_price=fp,
        actual_fill_size=order_result.filled_size,
        slippage_pct=slip,
        confirmed=not unfilled_cancelled,
        high_slippage_warning=high_slip,
        unfilled_cancelled=unfilled_cancelled,
    )


def _default_poll_kalshi(order_id: str) -> Dict[str, Any]:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    return KalshiClient().get_order(order_id)


def _default_cancel_kalshi(order_id: str) -> None:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    KalshiClient().cancel_order(order_id)


def monitor_position(
    position: OpenPosition,
    *,
    save: bool = True,
) -> None:
    data = load_positions()
    ops: List[Dict[str, Any]] = list(data.get("open_positions") or [])
    ops.append(
        {
            "position_id": position.position_id,
            "outlet": position.outlet,
            "market_id": position.market_id,
            "side": position.side,
            "entry_price": position.entry_price,
            "shares": position.shares,
            "notional_usd": position.notional_usd,
            "order_id": position.order_id,
            "opened_at": position.opened_at,
            "strategy_key": position.strategy_key,
            "hunt_types": position.hunt_types,
            "market_category": position.market_category,
            "expected_edge": position.expected_edge,
            "condition_id": position.condition_id,
            "token_id": position.token_id,
            "margin_borrowed_usd": position.margin_borrowed_usd,
            "claude_reasoning": position.claude_reasoning,
            "claude_confidence": position.claude_confidence,
            "claude_true_probability": position.claude_true_probability,
            "claude_decision": position.claude_decision,
            "journal_trade_id": position.journal_trade_id,
        }
    )
    data["open_positions"] = ops
    if save:
        save_positions(data)


def handle_resolution(
    position: OpenPosition,
    outcome: str,
    pnl: float,
    *,
    trade_id: str,
    strategy_key: str,
    hunt_types: list,
    market_category: str,
    hour_utc: Optional[int] = None,
) -> None:
    from datetime import datetime, timezone

    from trading_ai.automation.telegram_trade_events import maybe_notify_trade_closed
    from trading_ai.shark.execution import hook_post_trade_resolution
    from trading_ai.shark.state_store import apply_win_loss_to_capital

    apply_win_loss_to_capital(pnl)

    data = load_positions()
    ops = [p for p in (data.get("open_positions") or []) if p.get("position_id") != position.position_id]
    data["open_positions"] = ops
    hist = list(data.get("history") or [])
    hunt_vals = [getattr(h, "value", str(h)) for h in (hunt_types or [])]
    hist.append(
        {
            "position_id": position.position_id,
            "outlet": position.outlet,
            "market_id": position.market_id,
            "outcome": outcome,
            "pnl": pnl,
            "closed_at": time.time(),
            "hunt_types": hunt_vals,
            "market_category": market_category,
        }
    )
    data["history"] = hist
    save_positions(data)

    win = pnl > 0
    hook_post_trade_resolution(
        trade_id,
        win=win,
        strategy=strategy_key,
        hunt_types=hunt_types,
        outlet=position.outlet,
        market_id=position.market_id,
        market_category=market_category,
        hour_utc=hour_utc,
        pnl_dollars=None,
        update_capital=False,
        margin_borrowed_usd=position.margin_borrowed_usd,
        claude_true_probability=position.claude_true_probability,
        claude_decision=position.claude_decision,
        position_side=position.side,
        journal_trade_id=position.journal_trade_id,
        resolution_outcome=outcome,
        journal_pnl_usd=pnl,
    )

    append_shark_audit_record(
        {
            "event": "resolution",
            "trade_id": trade_id,
            "position_id": position.position_id,
            "pnl": pnl,
            "outcome": outcome,
        }
    )

    jid = (position.journal_trade_id or "").strip() or str(trade_id).strip()
    notional = float(position.notional_usd or 0.0)
    roi = (pnl / notional * 100.0) if notional > 1e-9 else 0.0
    ps = str(position.side or "yes").lower()
    pos_lbl = "YES" if ps == "yes" else "NO"
    payout_dollars = notional + pnl
    tick = str(position.market_id or "").strip()
    if ":" in tick:
        tick = tick.split(":")[-1]
    try:
        maybe_notify_trade_closed(
            None,
            {
                "trade_id": jid,
                "result": "win" if win else "loss",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market": f"{position.outlet}: {tick}",
                "ticker": tick,
                "position": pos_lbl,
                "gross_pnl_dollars": pnl,
                "net_pnl_dollars": pnl,
                "capital_allocated": notional,
                "roi_percent": roi,
                "payout_dollars": payout_dollars,
            },
        )
    except Exception:
        logger.exception("post_trade closed notify failed")

    check_drawdown_after_resolution()


def build_open_position_from_intent(
    intent: ExecutionIntent,
    order: OrderResult,
    conf: ConfirmationResult,
) -> OpenPosition:
    return OpenPosition(
        position_id=str(uuid.uuid4()),
        outlet=intent.outlet,
        market_id=intent.market_id,
        side=intent.side,
        entry_price=conf.actual_fill_price,
        shares=conf.actual_fill_size,
        notional_usd=intent.notional_usd,
        order_id=order.order_id,
        opened_at=time.time(),
        strategy_key="shark_default",
        hunt_types=[h.value for h in intent.hunt_types],
        market_category=intent.meta.get("market_category", "default"),
        expected_edge=intent.edge_after_fees,
        condition_id=intent.meta.get("condition_id"),
        token_id=intent.meta.get("token_id"),
        margin_borrowed_usd=float(intent.meta.get("margin_borrowed", 0.0)),
        claude_reasoning=intent.meta.get("claude_reasoning"),
        claude_confidence=intent.meta.get("claude_confidence"),
        claude_true_probability=intent.meta.get("claude_true_probability"),
        claude_decision=intent.meta.get("claude_decision"),
        journal_trade_id=str(intent.meta.get("trade_id") or "") or None,
    )


def calculate_pnl(position: OpenPosition, outcome: str) -> float:
    """Binary contract P&L from resolution outcome string."""
    o = (outcome or "").strip().upper()
    yes_win = o in ("YES", "Y", "TRUE", "1", "WIN")
    no_win = o in ("NO", "N", "FALSE", "0", "LOSS")
    px = max(position.entry_price, 1e-9)
    stake = position.notional_usd
    if position.side.lower() == "yes":
        if yes_win:
            return stake * (1.0 / px - 1.0)
        if no_win:
            return -stake
    else:
        p_no = max(1.0 - px, 1e-9)
        if no_win:
            return stake * (1.0 / p_no - 1.0)
        if yes_win:
            return -stake
    return 0.0


def poll_resolution_for_outlet(outlet: str, market_id: str, position: OpenPosition) -> Optional[str]:
    """Return outcome string if resolved, else None."""
    o = outlet.lower()
    if o == "polymarket":
        return _poll_poly(market_id)
    if o == "kalshi":
        return _poll_kalshi(market_id)
    if o == "manifold":
        return _poll_manifold(market_id)
    return None


def _poll_poly(condition_id: str) -> Optional[str]:
    cid = condition_id.replace("poly:", "")
    url = f"https://clob.polymarket.com/markets/{urllib.parse.quote(cid, safe='')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        m = j if isinstance(j, dict) else {}
        if m.get("resolved") or m.get("closed"):
            return str(m.get("winner") or m.get("outcome") or "yes")
    except Exception:
        pass
    return None


def _poll_kalshi(ticker: str) -> Optional[str]:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    t = ticker.split(":")[-1]
    try:
        j = KalshiClient().get_market(t)
        st = str(j.get("status") or "").lower()
        if st == "finalized":
            return str(j.get("result") or "yes")
    except Exception:
        pass
    return None


def _poll_manifold(contract_id: str) -> Optional[str]:
    cid = contract_id.replace("manifold:", "")
    url = f"https://api.manifold.markets/v0/market/{urllib.parse.quote(cid, safe='')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        if j.get("isResolved"):
            return str(j.get("resolution") or "YES")
    except Exception:
        pass
    return None
