"""Block trades where fees + slippage dominate expected edge (negative expectation)."""

from __future__ import annotations

import os
from typing import Any


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def check_fee_dominance_pre_trade(
    *,
    notional_usd: float,
    net_edge_bps: float,
    est_round_trip_cost_bps: float,
    spread_bps: float,
) -> tuple[bool, str]:
    """
    ``expected_profit`` proxy: ``notional * net_edge_bps / 1e4``.
    Costs: round-trip fee bps + half-spread as slippage proxy (bps on notional).
    """
    if notional_usd <= 0:
        return False, "zero_notional"

    edge_bps = float(net_edge_bps)
    if edge_bps <= 0:
        return False, "non_positive_net_edge_bps"
    rt_cost = float(est_round_trip_cost_bps)
    slip_bps = float(spread_bps) * 0.5
    buffer_bps = _env_float("REALITY_LOCK_FEE_DOMINANCE_BUFFER_BPS", 0.0)

    expected_profit = notional_usd * (edge_bps / 10000.0)
    fees_quote = notional_usd * (rt_cost / 10000.0)
    slip_quote = notional_usd * (slip_bps / 10000.0)

    if expected_profit <= fees_quote + slip_quote + notional_usd * (buffer_bps / 10000.0):
        return False, "expected_profit<=fees_plus_slippage"
    return True, "ok"


def fee_dominance_from_dec(
    notional_usd: float,
    dec: Any,
    feat_spread_bps: float,
) -> tuple[bool, str]:
    """Adapter for :class:`~trading_ai.nte.strategies.ab_router.RouterDecision`."""
    try:
        ne = float(getattr(dec, "net_edge_bps", 0.0) or 0.0)
        rt = float(getattr(dec, "est_round_trip_cost_bps", 0.0) or 0.0)
    except (TypeError, ValueError):
        ne, rt = 0.0, 0.0
    return check_fee_dominance_pre_trade(
        notional_usd=notional_usd,
        net_edge_bps=ne,
        est_round_trip_cost_bps=rt,
        spread_bps=float(feat_spread_bps),
    )
