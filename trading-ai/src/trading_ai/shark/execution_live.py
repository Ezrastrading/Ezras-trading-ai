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
from trading_ai.shark.state_store import load_capital, load_positions, save_positions

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
    Polymarket: ``submit_polymarket_order`` (requires ``POLY_WALLET_KEY`` for execution; optional for scan-only).
    Manifold: ``MANIFOLD_API_KEY`` (``submit_manifold_bet``).
    """
    o = (intent.outlet or "").lower()
    if o == "kalshi":
        from trading_ai.shark.outlets.kalshi import KalshiClient

        client = KalshiClient()
        ticker = intent.market_id
        if ":" in ticker:
            ticker = ticker.split(":")[-1]
        yes_cents = int(round(intent.expected_price * 100)) if intent.side == "yes" else int(round((1.0 - intent.expected_price) * 100))
        return client.place_order(
            ticker=ticker,
            side=intent.side,
            count=max(1, int(intent.shares)),
            yes_price_cents=max(1, min(99, yes_cents)),
        )
    if o == "polymarket":
        from trading_ai.shark.polymarket_live import submit_polymarket_order

        return submit_polymarket_order(intent)
    if o == "manifold":
        if not manifold_real_money_execution_enabled():
            logger.info("Manifold skipped — play money only")
            raise ValueError("manifold_play_money_skip")
        from trading_ai.shark.manifold_live import submit_manifold_bet

        return submit_manifold_bet(intent)
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
    from trading_ai.shark.execution import hook_post_trade_resolution
    from trading_ai.shark.reporting import format_loss_resolved, format_win_resolved, send_telegram_live
    from trading_ai.shark.state_store import apply_win_loss_to_capital

    rec_before = load_capital()
    apply_win_loss_to_capital(pnl)
    rec_after = load_capital()

    data = load_positions()
    ops = [p for p in (data.get("open_positions") or []) if p.get("position_id") != position.position_id]
    data["open_positions"] = ops
    hist = list(data.get("history") or [])
    hist.append(
        {
            "position_id": position.position_id,
            "outlet": position.outlet,
            "market_id": position.market_id,
            "outcome": outcome,
            "pnl": pnl,
            "closed_at": time.time(),
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

    if win:
        send_telegram_live(
            format_win_resolved(
                pnl=pnl,
                ret_pct=pnl / max(rec_before.current_capital, 1e-9),
                capital=rec_after.current_capital,
                day_pnl=pnl,
            )
        )
    else:
        send_telegram_live(
            format_loss_resolved(pnl=pnl, capital=rec_after.current_capital, cluster_status="ok")
        )

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
