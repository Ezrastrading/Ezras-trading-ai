"""Post-fee net edge gate — reject entries when spread + fees dominate expected move."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_ai.nte.config.coinbase_avenue1_launch import CoinbaseAvenue1Launch


@dataclass(frozen=True)
class NetEdgeResult:
    allowed: bool
    expected_net_edge_bps: float
    spread_bps: float
    est_round_trip_cost_bps: float
    reason: str


def estimate_round_trip_cost_bps(
    *,
    spread_bps: float,
    maker_fee_pct: float,
    assume_maker_entry: bool = True,
    taker_fee_pct: float = 0.0025,
) -> float:
    """Conservative: spread + fees on entry and exit (maker both sides if resting)."""
    fee_pct = maker_fee_pct if assume_maker_entry else taker_fee_pct
    fee_bps = fee_pct * 10000.0 * 2.0
    return spread_bps + fee_bps


def evaluate_net_edge(
    *,
    spread_pct: float,
    expected_edge_bps: float,
    strategy_min_net_bps: float,
    launch: CoinbaseAvenue1Launch,
    assume_maker_entry: bool = True,
) -> NetEdgeResult:
    spread_bps = float(spread_pct) * 10000.0
    cost = estimate_round_trip_cost_bps(
        spread_bps=spread_bps,
        maker_fee_pct=launch.fees.estimated_maker_fee_pct,
        taker_fee_pct=launch.fees.estimated_taker_fee_pct,
        assume_maker_entry=assume_maker_entry,
    )
    mult = float(launch.fees.required_edge_multiple_of_estimated_cost)
    net = float(expected_edge_bps) - cost
    need_multiple = float(expected_edge_bps) >= cost * mult - 1e-9
    need_min_net = net >= strategy_min_net_bps - 1e-9
    allowed = need_multiple and need_min_net
    reason = "ok" if allowed else (
        f"net {net:.1f}bps need>={strategy_min_net_bps} mult_ok={need_multiple}"
    )
    return NetEdgeResult(
        allowed=allowed,
        expected_net_edge_bps=net,
        spread_bps=spread_bps,
        est_round_trip_cost_bps=cost,
        reason=reason,
    )


def rough_expected_move_bps_from_z(z: float, *, scale: float = 12.0) -> float:
    """Heuristic edge proxy from z-score distance (tune with live data)."""
    return abs(float(z)) * scale
