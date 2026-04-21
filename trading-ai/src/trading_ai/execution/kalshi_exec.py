"""
Route BUY decisions to Kalshi only (never Polymarket execution).

Uses account position sizing policy (:func:`compute_sizing_decision_for_trade`) so the venue
``count`` is derived from **approved_size** (dollars), not the legacy default contract count alone.

Returns a small status dict for logging and events. Never raises.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict

from trading_ai.automation.position_sizing_policy import compute_sizing_decision_for_trade
from trading_ai.clients import kalshi as kalshi_client
from trading_ai.config import Settings
from trading_ai.execution.submission_audit import append_execution_submission_log
from trading_ai.market.cross_venue_map import resolve_kalshi_ticker
from trading_ai.models.schemas import CandidateMarket, TradeBrief, TradeDecisionView

logger = logging.getLogger(__name__)


def _trade_id_for_sizing(market: CandidateMarket) -> str:
    return f"kalshi-exec-{market.market_id}"[:200]


def _requested_dollars_from_settings(settings: Settings, yes_price: float) -> float:
    """
    Map legacy ``kalshi_default_order_size`` (contract-count intent) to a dollar request at this price.

    ``requested_dollars = default_order_size * yes_price`` preserves prior scale when bucket is NORMAL
    (policy then approves the same notional, and contracts = approved / price).
    """
    return float(settings.kalshi_default_order_size) * float(yes_price)


def _approved_dollars_to_contracts(approved_dollars: float, yes_price: float) -> int:
    """Convert approved dollar notional to integer Kalshi contracts (floor; min one contract if it fits)."""
    if approved_dollars <= 0 or yes_price <= 0:
        return 0
    n = math.floor(approved_dollars / yes_price)
    return int(n) if n >= 1 else 0


def execute_buy_on_kalshi(
    settings: Settings,
    market: CandidateMarket,
    brief: TradeBrief,
    decision: TradeDecisionView,
) -> Dict[str, Any]:
    """
    If decision is BUY_* and execution is enabled, submit to Kalshi.
    Polymarket is never used for orders here.
    """
    out: Dict[str, Any] = {
        "execution_platform": "paper",
        "kalshi_order_id": None,
        "kalshi_execution_error": None,
    }
    if decision.action not in ("BUY_YES", "BUY_NO"):
        return out

    if settings.kalshi_enabled:
        out["execution_platform"] = "kalshi"

    if not settings.kalshi_enabled:
        out["kalshi_execution_error"] = "kalshi_disabled"
        return out

    ticker = resolve_kalshi_ticker(settings, market)
    if not ticker:
        out["kalshi_execution_error"] = "no_kalshi_ticker_mapping"
        return out

    if not settings.kalshi_execution_enabled:
        out["kalshi_execution_error"] = "execution_disabled_dry_run"
        return out

    px = kalshi_client.get_market_price(settings, ticker)
    if px is None:
        out["kalshi_execution_error"] = "price_unavailable"
        return out

    tid = _trade_id_for_sizing(market)
    requested_dollars = _requested_dollars_from_settings(settings, px)
    sizing_trade: Dict[str, Any] = {
        "trade_id": tid,
        "capital_allocated": requested_dollars,
    }
    d = compute_sizing_decision_for_trade(sizing_trade)

    st = str(d.get("approval_status") or "")
    ta = d.get("trading_allowed")
    try:
        appr = float(d.get("approved_size") or 0.0)
    except (TypeError, ValueError):
        appr = -1.0

    if st == "BLOCKED" or ta is False or appr <= 0.0:
        append_execution_submission_log(
            trade_id=tid,
            requested_size=d.get("requested_size"),
            approved_size=d.get("approved_size"),
            actual_submitted_size=0,
            bucket=d.get("effective_bucket"),
            approval_status=st,
            trading_allowed=ta,
            reason=d.get("reason"),
            extra={
                "venue": "kalshi",
                "venue_unit": "contracts",
                "submission_aborted": True,
                "abort_reason": "sizing_blocked_or_not_trading",
                "yes_price": px,
            },
        )
        out["kalshi_execution_error"] = "sizing_blocked_or_zero_approved"
        out["sizing_decision"] = d
        return out

    contracts = _approved_dollars_to_contracts(appr, px)
    if contracts < 1:
        append_execution_submission_log(
            trade_id=tid,
            requested_size=d.get("requested_size"),
            approved_size=appr,
            actual_submitted_size=0,
            bucket=d.get("effective_bucket"),
            approval_status=st,
            trading_allowed=ta,
            reason=d.get("reason"),
            extra={
                "venue": "kalshi",
                "venue_unit": "contracts",
                "submission_aborted": True,
                "abort_reason": "approved_notional_below_one_contract",
                "yes_price": px,
            },
        )
        out["kalshi_execution_error"] = "approved_notional_below_one_contract"
        out["sizing_decision"] = d
        return out

    append_execution_submission_log(
        trade_id=tid,
        requested_size=d.get("requested_size"),
        approved_size=appr,
        actual_submitted_size=contracts,
        bucket=d.get("effective_bucket"),
        approval_status=st,
        trading_allowed=ta,
        reason=d.get("reason"),
        extra={"venue": "kalshi", "venue_unit": "contracts", "yes_price": px},
    )

    try:
        res = kalshi_client.place_order(settings, ticker, decision.action, contracts)
    except Exception as exc:
        logger.exception("Kalshi execution failed")
        out["kalshi_execution_error"] = str(exc)
        out["sizing_decision"] = d
        return out

    out["submitted_contracts"] = contracts
    out["approved_size_dollars"] = appr
    out["sizing_decision"] = d

    if res.get("ok") and res.get("order"):
        oid = (res.get("order") or {}).get("order_id")
        out["kalshi_order_id"] = oid
        out["kalshi_execution_error"] = None
    else:
        out["kalshi_execution_error"] = res.get("error") or "unknown"
    return out
