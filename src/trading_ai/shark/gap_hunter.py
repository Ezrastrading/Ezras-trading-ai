"""
Structural gap hunter — 24/7 passive monitoring. Standard scan 15 min; gap-active 30s.
No trading windows. No clock-based blocking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.shark.models import GapObservation, StructuralGapPattern

GAP_STANDARD_SCAN_SECONDS = 15 * 60
GAP_ACTIVE_SCAN_SECONDS = 30.0


def competition_inverse(level: str) -> float:
    m = {"none": 1.0, "some": 0.6, "heavy": 0.2}
    return m.get(level, 0.5)


def gap_score(observations: List[GapObservation]) -> float:
    if not observations:
        return 0.0
    lag = sum(o.lag_seconds for o in observations) / len(observations)
    cons = sum(o.consistency_hint for o in observations) / len(observations)
    vol = sum(o.volume_available for o in observations) / len(observations)
    edge = sum(o.edge_per_trade for o in observations) / len(observations)
    comp = sum(competition_inverse(o.competition) for o in observations) / len(observations)
    lag_n = min(1.0, lag / 300.0)
    vol_n = min(1.0, math.log10(1.0 + vol) / 5.0)
    return (
        lag_n * 0.20
        + cons * 0.30
        + vol_n * 0.20
        + min(1.0, edge * 5.0) * 0.20
        + comp * 0.10
    )


def detect_oracle_lag(
    *,
    settlement_value: float,
    market_yes_price: float,
    lag_seconds: float,
    volume_available: float,
    edge_per_trade: float,
    competition: str = "none",
) -> GapObservation:
    _ = settlement_value, market_yes_price
    return GapObservation(
        gap_type="oracle_lag",
        lag_seconds=lag_seconds,
        consistency_hint=1.0 if lag_seconds > 5 else 0.2,
        volume_available=volume_available,
        edge_per_trade=edge_per_trade,
        competition=competition,
    )


def detect_new_market_immaturity(
    *,
    deviation_from_external: float,
    days_since_open: float,
    volume_available: float,
    competition: str = "some",
) -> Optional[GapObservation]:
    if days_since_open > 30 or deviation_from_external < 0.10:
        return None
    return GapObservation(
        gap_type="new_market_immaturity",
        lag_seconds=30.0,
        consistency_hint=min(1.0, deviation_from_external * 3.0),
        volume_available=volume_available,
        edge_per_trade=deviation_from_external,
        competition=competition,
    )


def detect_cross_platform_sync_gap(
    *,
    lag_seconds: float,
    volume_available: float,
    edge_per_trade: float,
    competition: str = "none",
) -> GapObservation:
    return GapObservation(
        gap_type="cross_platform_sync",
        lag_seconds=lag_seconds,
        consistency_hint=0.8,
        volume_available=volume_available,
        edge_per_trade=edge_per_trade,
        competition=competition,
    )


def detect_resolution_data_lag(
    *,
    public_data_value: float,
    market_yes_price: float,
    lag_seconds: float,
    volume_available: float,
    competition: str = "none",
) -> GapObservation:
    edge = abs(public_data_value - market_yes_price)
    return GapObservation(
        gap_type="resolution_data_lag",
        lag_seconds=lag_seconds,
        consistency_hint=1.0 if lag_seconds > 5 else 0.3,
        volume_available=volume_available,
        edge_per_trade=edge,
        competition=competition,
    )


@dataclass
class GapExploitationState:
    active: bool = False
    gap_type: str = ""
    gap_exposure_fraction: float = 0.0
    recent_win_rates: List[bool] = field(default_factory=list)
    baseline_lag_seconds: float = 0.0
    competition: str = "none"


def confirm_pattern(observations: List[GapObservation], min_obs: int = 5) -> bool:
    return len(observations) >= min_obs and gap_score(observations) > 0.75


def confirm_gap_with_win_rate(
    observations: List[GapObservation],
    trade_wins: List[bool],
) -> bool:
    """Minimum 5 observations, score > 0.75, win rate > 80% on qualifying test trades."""
    if len(observations) < 5 or gap_score(observations) <= 0.75:
        return False
    if not trade_wins:
        return False
    wr = sum(1 for w in trade_wins if w) / len(trade_wins)
    return wr > 0.80


def should_escalate(observations: List[GapObservation]) -> Tuple[bool, float]:
    sc = gap_score(observations)
    return confirm_pattern(observations), sc


def gap_closure_triggers(
    *,
    recent_trades: List[bool],
    baseline_lag: float,
    current_lag: float,
    competition: str,
) -> Tuple[bool, str]:
    if len(recent_trades) >= 10:
        wr = sum(1 for x in recent_trades[-10:] if x) / 10.0
        if wr < 0.65:
            return True, "win_rate_below_65pct_10"
    if baseline_lag > 0 and current_lag < 0.4 * baseline_lag:
        return True, "lag_compressed_60pct"
    if competition == "heavy":
        return True, "heavy_competition"
    return False, ""


def gap_exploitation_scan_interval(state: GapExploitationState) -> float:
    return GAP_ACTIVE_SCAN_SECONDS if state.active else GAP_STANDARD_SCAN_SECONDS


def gap_kelly_multiplier() -> float:
    return 0.80


def scan_for_gaps_stub() -> List[GapObservation]:
    """Wire real monitors (oracle feeds, cross-outlet sync) in production."""
    return []
