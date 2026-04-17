"""
Kalshi Gate A — high ROI hunter (legacy filename: ``kalshi_scalable_gate``).

Scans all open markets for trades with minimum ROI (default 400%%), contract cost cap,
3–5 trades per day spread across the session, Claude+GPT confirmation via Gate B helpers.
State for daily cadence: ``shark/state/kalshi_gate_a_state.json``.
Open positions use the same file as Gate B: ``kalshi_gate_b_state.json``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

MIN_ROI_PCT = float(os.environ.get("KALSHI_GA_MIN_ROI", "400.0"))
MAX_CONTRACT_COST = float(os.environ.get("KALSHI_GA_MAX_CONTRACT_COST", "0.80"))
MIN_CONTRACT_COST = float(os.environ.get("KALSHI_GA_MIN_CONTRACT_COST", "0.01"))
MIN_TRADES_PER_DAY = int(os.environ.get("KALSHI_GA_MIN_TRADES_DAY", "3"))
MAX_TRADES_PER_DAY = int(os.environ.get("KALSHI_GA_MAX_TRADES_DAY", "5"))
MIN_CONTRACTS = int(os.environ.get("KALSHI_GA_MIN_CONTRACTS", "5"))
ALLOCATION_PCT = float(os.environ.get("KALSHI_GA_ALLOCATION_PCT", "0.20"))
TTR_MIN = int(os.environ.get("KALSHI_GA_TTR_MIN", "60"))
TTR_MAX = int(os.environ.get("KALSHI_GA_TTR_MAX", "86400"))
MAX_MARKETS_FETCH = int(os.environ.get("KALSHI_GA_MAX_MARKETS", "8000"))
TOP_CANDIDATES = int(os.environ.get("KALSHI_GA_TOP_CANDIDATES", "10"))

SKIP_SUBSTR = tuple(
    x.strip().upper()
    for x in (os.environ.get("KALSHI_GA_SKIP_TICKER_SUBSTR") or "").split(",")
    if x.strip()
)


def _tz_name() -> str:
    return (os.environ.get("SHARK_TZ") or "UTC").strip() or "UTC"


def _now_local() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(_tz_name()))
    except Exception:
        return datetime.utcnow()


def _ga_daily_path() -> Path:
    return shark_state_path("kalshi_gate_a_state.json")


def _default_ga_daily() -> Dict[str, Any]:
    return {
        "date": "",
        "trades_today": 0,
        "filled_hours": [],  # type: ignore[list-item]
        "trade_slots": [],
    }


def _load_ga_daily() -> Dict[str, Any]:
    p = _ga_daily_path()
    if not p.is_file():
        return _default_ga_daily()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            s = _default_ga_daily()
            s.update(raw)
            return s
    except Exception as exc:
        logger.warning("kalshi_gate_a_state load: %s", exc)
    return _default_ga_daily()


def _save_ga_daily(state: Dict[str, Any]) -> None:
    try:
        _ga_daily_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("kalshi_gate_a_state save: %s", exc)


def _get_trade_slots(n_trades: int) -> List[int]:
    n = max(1, min(24, int(n_trades)))
    return [int(i * 24 / n) for i in range(n)]


def _should_trade_now() -> bool:
    """Spread trades across the day; optional end-of-day catch-up."""
    st = _load_ga_daily()
    today = _now_local().strftime("%Y-%m-%d")
    current_hour = _now_local().hour

    if today != st.get("date"):
        st["date"] = today
        st["trades_today"] = 0
        st["filled_hours"] = []
        st["trade_slots"] = _get_trade_slots(MAX_TRADES_PER_DAY)
        _save_ga_daily(st)

    trades_today = int(st.get("trades_today") or 0)
    if trades_today >= MAX_TRADES_PER_DAY:
        return False

    filled = {int(h) for h in (st.get("filled_hours") or []) if h is not None}
    slots = list(st.get("trade_slots") or _get_trade_slots(MAX_TRADES_PER_DAY))
    slot_set = set(slots)

    if current_hour in slot_set and current_hour not in filled:
        return True

    trades_needed = MIN_TRADES_PER_DAY - trades_today
    hours_left = 23 - current_hour
    if trades_needed > 0 and hours_left <= trades_needed:
        logger.info(
            "Gate A: forcing trade — %d needed, %d hours left",
            trades_needed,
            hours_left,
        )
        return True

    return False


def _mark_traded_this_hour(*, slot_trade: bool) -> None:
    st = _load_ga_daily()
    h = _now_local().hour
    if slot_trade:
        filled = list(st.get("filled_hours") or [])
        if h not in filled:
            filled.append(h)
        st["filled_hours"] = filled
    st["trades_today"] = int(st.get("trades_today") or 0) + 1
    _save_ga_daily(st)


def _roi_pct(cost: float) -> float:
    if cost <= 0:
        return 0.0
    return (1.0 - cost) / cost * 100.0


def _scan_all_markets_gate_a(
    markets: List[Dict[str, Any]],
    balance: float,
) -> List[Dict[str, Any]]:
    from trading_ai.shark.kalshi_gate_b import _get_ttr_sec

    trade_size = min(max(0.0, float(balance)) * ALLOCATION_PCT, 100.0)
    candidates: List[Dict[str, Any]] = []

    for m in markets:
        if not isinstance(m, dict):
            continue
        ticker = str(m.get("ticker") or "")
        tu = ticker.upper()
        if any(s in tu for s in SKIP_SUBSTR if s):
            continue

        close_str = str(m.get("close_time") or "")
        if any(y in close_str for y in ("2027", "2028", "2029")):
            continue

        status = str(m.get("status") or "").strip().lower()
        if status and status not in ("open", "active", ""):
            continue

        yes_ask = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0.0)
        no_ask = float(m.get("no_ask_dollars") or m.get("no_ask") or 0.0)
        if yes_ask > 1.0:
            yes_ask /= 100.0
        if no_ask > 1.0:
            no_ask /= 100.0

        if yes_ask <= 0 or no_ask <= 0:
            continue

        best_side: str | None = None
        best_cost = 0.0
        best_prob = 0.0
        best_roi = -1.0

        for side, cost in (("yes", yes_ask), ("no", no_ask)):
            if not (MIN_CONTRACT_COST <= cost <= MAX_CONTRACT_COST):
                continue
            roi = _roi_pct(cost)
            if roi < MIN_ROI_PCT:
                continue
            if roi > best_roi:
                best_roi = roi
                best_side = side
                best_cost = cost
                best_prob = cost

        if not best_side:
            continue

        ttr = _get_ttr_sec(m)
        if not (TTR_MIN <= ttr <= TTR_MAX):
            continue

        contracts = max(MIN_CONTRACTS, int(trade_size / max(best_cost, 1e-6)))
        total_cost = contracts * best_cost
        max_payout = float(contracts)
        expected = max_payout * best_prob

        candidates.append(
            {
                "ticker": ticker,
                "title": str(m.get("title") or m.get("subtitle") or ""),
                "side": best_side,
                "probability": best_prob,
                "contract_cost": best_cost,
                "roi_pct": best_roi,
                "ttr": ttr,
                "contracts": contracts,
                "total_cost": total_cost,
                "max_payout": max_payout,
                "expected_payout": expected,
                "trade_size": trade_size,
                "balance": balance,
                "market": m,
            }
        )

    candidates.sort(key=lambda x: -float(x["roi_pct"]))
    logger.info(
        "Gate A scan: %d candidates (>= %.0f%% ROI) from %d markets",
        len(candidates),
        MIN_ROI_PCT,
        len(markets),
    )
    return candidates


def _place_gate_a_trade(
    trade: Dict[str, Any],
    kalshi_client: Any,
    balance: float,
    ai_reason: str,
) -> bool:
    from trading_ai.shark.kalshi_gate_b import _load_state, _save_state
    from trading_ai.shark.outlets.kalshi import KalshiClient

    if not isinstance(kalshi_client, KalshiClient):
        return False

    ticker = str(trade["ticker"])
    side = str(trade["side"]).lower()
    contracts = int(trade["contracts"])
    total_cost = float(trade["total_cost"])
    expected = float(trade["expected_payout"])
    roi = float(trade["roi_pct"])
    prob = float(trade["probability"])
    ttr = int(trade["ttr"])
    title = str(trade["title"])

    state = _load_state()
    open_trades: Dict[str, Any] = dict(state.get("open_trades") or {})
    if ticker in open_trades:
        return False

    try:
        res = kalshi_client.place_order(
            ticker=ticker,
            side=side,
            count=contracts,
            action="buy",
            order_type="market",
            skip_pretrade_buy_gates=True,
            min_order_prob=0.01,
        )
        oid = str(res.order_id or "")
        if not res.success or not oid:
            return False

        open_trades[ticker] = {
            "ticker": ticker,
            "title": title,
            "side": side,
            "contracts": contracts,
            "contract_cost": trade["contract_cost"],
            "total_cost": total_cost,
            "expected_payout": expected,
            "roi_pct": roi,
            "probability": prob,
            "ttr": ttr,
            "entry_time": time.time(),
            "order_id": oid,
            "gate": "A",
        }
        state["open_trades"] = open_trades
        _save_state(state)

        try:
            from trading_ai.shark.supabase_logger import log_trade

            log_trade(
                platform="kalshi",
                gate="A",
                product_id=ticker,
                side=side,
                strategy="gate_a_high_roi",
                entry_price=float(trade["contract_cost"]),
                exit_price=0.0,
                size_usd=total_cost,
                pnl_usd=0.0,
                exit_reason="open",
                hold_seconds=0,
                balance_after=balance,
                metadata={
                    "contracts": contracts,
                    "roi_pct": roi,
                    "prob": prob,
                    "ttr_min": ttr // 60,
                    "ai_approval": ai_reason[:500],
                },
            )
        except Exception:
            pass

        try:
            from trading_ai.shark.reporting import send_telegram

            td = int(_load_ga_daily().get("trades_today") or 0) + 1
            send_telegram(
                f"🏆 KALSHI GATE A\n"
                f"{'─'*32}\n"
                f"Market: {title[:60]}\n"
                f"Bet: {side.upper()} ({prob*100:.0f}% implied)\n"
                f"Contracts: {contracts}\n"
                f"Cost/contract: ${float(trade['contract_cost']):.3f}\n"
                f"Total cost: ${total_cost:.2f}\n"
                f"Max payout: ${contracts:.0f}\n"
                f"Expected: ${expected:.2f}\n"
                f"ROI: {roi:.0f}%\n"
                f"Resolves: ~{ttr//60} min\n"
                f"AI: {ai_reason[:200]}\n"
                f"{'─'*32}\n"
                f"Trade {td}/{MAX_TRADES_PER_DAY} today"
            )
        except Exception:
            pass

        logger.info(
            "Gate A PLACED: %s %s %d contracts $%.2f ROI=%.0f%%",
            ticker,
            side.upper(),
            contracts,
            total_cost,
            roi,
        )
        return True
    except Exception as exc:
        logger.warning("Gate A place error: %s", exc)
        return False


def run_gate_a(
    kalshi_client: Any,
    markets: List[Dict[str, Any]],
    balance: float,
) -> int:
    """Resolve first, then maybe one Gate A trade if schedule + candidates + AI allow."""
    if (os.environ.get("KALSHI_GATE_A_ENABLED") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return 0

    from trading_ai.shark.kalshi_gate_b import (
        _confirm_trade,
        check_resolutions,
    )
    from trading_ai.shark.kalshi_gate_b import _ensure_asks
    from trading_ai.shark.outlets.kalshi import KalshiClient

    if not isinstance(kalshi_client, KalshiClient) or not kalshi_client.has_kalshi_credentials():
        return 0

    resolved = check_resolutions(kalshi_client, balance)

    if not _should_trade_now():
        return resolved

    enriched: List[Dict[str, Any]] = []
    cap = max(500, min(12000, MAX_MARKETS_FETCH))
    for m in markets[:cap]:
        if not isinstance(m, dict):
            continue
        enriched.append(_ensure_asks(kalshi_client, m))

    candidates = _scan_all_markets_gate_a(enriched, balance)
    if not candidates:
        logger.info("Gate A: no qualifying trades in %d markets", len(enriched))
        return resolved

    st0 = _load_ga_daily()
    slot_set = set(st0.get("trade_slots") or _get_trade_slots(MAX_TRADES_PER_DAY))
    slot_trade = _now_local().hour in slot_set

    for candidate in candidates[:TOP_CANDIDATES]:
        approved, reason = _confirm_trade(candidate, balance)
        if not approved:
            logger.info("Gate A skip %s: %s", candidate["ticker"], reason)
            continue
        if _place_gate_a_trade(candidate, kalshi_client, balance, reason):
            _mark_traded_this_hour(slot_trade=slot_trade)
            break

    return resolved


def _available_balance() -> float:
    try:
        from trading_ai.shark.capital_effective import effective_capital_for_outlet
        from trading_ai.shark.state_store import load_capital

        book = load_capital()
        return max(0.0, effective_capital_for_outlet("kalshi", float(book.current_capital)))
    except Exception:
        raw = (os.environ.get("KALSHI_ACTUAL_BALANCE") or "0").strip()
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 0.0


def run_gate_a_job_fetch() -> int:
    """Fetch open markets and run Gate A (scheduler entrypoint)."""
    from trading_ai.shark.outlets.kalshi import KalshiClient

    cap = max(500, min(12000, MAX_MARKETS_FETCH))
    client = KalshiClient()
    if not client.has_kalshi_credentials():
        return 0
    markets = client.fetch_all_open_markets(max_rows=cap)
    bal = _available_balance()
    return run_gate_a(client, markets, bal)


# Back-compat name used by older docs
def run_scalable_gate(kalshi_client: Any, balance: float) -> int:
    cap = max(500, min(12000, MAX_MARKETS_FETCH))
    markets = kalshi_client.fetch_all_open_markets(max_rows=cap) if hasattr(
        kalshi_client, "fetch_all_open_markets"
    ) else []
    return run_gate_a(kalshi_client, markets, balance)
