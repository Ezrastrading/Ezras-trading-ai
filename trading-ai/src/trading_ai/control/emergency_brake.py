"""
Emergency brake — hard degradation / halt signals. Percent-based rules; no martingale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from trading_ai.control.operating_mode_types import OperatingMode, OperatingModeConfig, OperatingSnapshot, mode_index


@dataclass
class BrakeEvaluation:
    triggered: bool
    recommended_floor: OperatingMode
    """Most conservative mode required this cycle."""
    reasons: List[str]
    severity: int


def _loss_rate(pnls: List[float]) -> float:
    if not pnls:
        return 0.0
    losses = sum(1 for p in pnls if p < 0)
    return losses / max(len(pnls), 1)


def _expectancy(pnls: List[float]) -> float:
    if not pnls:
        return 0.0
    return sum(pnls) / len(pnls)


def _drawdown_pct(high: float, cur: float) -> float:
    if high <= 0:
        return 0.0
    return max(0.0, (high - cur) / high)


def _more_conservative(a: OperatingMode, b: OperatingMode) -> OperatingMode:
    """Lower :func:`mode_index` = more conservative (HALTED first in MODE_ORDER)."""
    return a if mode_index(a) <= mode_index(b) else b


def evaluate_emergency_brake(
    snap: OperatingSnapshot,
    cfg: OperatingModeConfig,
) -> BrakeEvaluation:
    reasons: List[str] = []
    severity = 0
    floor = OperatingMode.AGGRESSIVE_CONFIRMED

    if snap.consecutive_losses >= cfg.max_consecutive_losses:
        reasons.append(f"loss_streak>={cfg.max_consecutive_losses}")
        severity = max(severity, 90)
        floor = _more_conservative(floor, OperatingMode.HALTED)

    n = min(cfg.loss_rate_window_n, len(snap.last_n_trade_pnls))
    window = snap.last_n_trade_pnls[-n:] if n else []
    lr = _loss_rate(window)
    if n >= 5 and lr >= cfg.max_loss_rate_last_n_trades:
        reasons.append(f"loss_rate_{lr:.0%}>={cfg.max_loss_rate_last_n_trades:.0%}_last_{n}")
        severity = max(severity, 85)
        floor = _more_conservative(floor, OperatingMode.HALTED)

    dd = _drawdown_pct(snap.rolling_equity_high, snap.current_equity)
    if dd >= cfg.max_rolling_drawdown_pct:
        reasons.append(f"drawdown_{dd:.1%}>={cfg.max_rolling_drawdown_pct:.1%}")
        severity = max(severity, 88)
        floor = _more_conservative(floor, OperatingMode.HALTED)

    exp_n = min(cfg.negative_expectancy_window_n, len(snap.last_n_trade_pnls))
    exp_window = snap.last_n_trade_pnls[-exp_n:] if exp_n else []
    ex = _expectancy(exp_window)
    if exp_n >= 8 and ex < 0:
        reasons.append(f"negative_expectancy_{ex:.4f}_over_{exp_n}_trades")
        severity = max(severity, 75)
        floor = _more_conservative(floor, OperatingMode.DEFENSIVE)

    if snap.slippage_health < cfg.execution_health_min:
        reasons.append("slippage_health_critical")
        severity = max(severity, 70)
        floor = _more_conservative(floor, OperatingMode.DEFENSIVE)
    if snap.execution_health < cfg.execution_health_min:
        reasons.append("execution_health_critical")
        severity = max(severity, 72)
        floor = _more_conservative(floor, OperatingMode.DEFENSIVE)

    if snap.reconciliation_failures_24h >= cfg.max_reconciliation_mismatches_24h:
        reasons.append("reconciliation_anomaly_threshold")
        severity = max(severity, 95)
        floor = _more_conservative(floor, OperatingMode.HALTED)
    if snap.databank_failures_24h >= 3:
        reasons.append("databank_failure_threshold")
        severity = max(severity, 92)
        floor = _more_conservative(floor, OperatingMode.HALTED)
    if snap.governance_blocks_24h >= cfg.max_governance_blocks_24h:
        reasons.append("governance_block_storm")
        severity = max(severity, 80)
        floor = _more_conservative(floor, OperatingMode.DEFENSIVE)
    if snap.blocked_orders_streak >= 5:
        reasons.append("repeated_blocked_orders")
        severity = max(severity, 78)
        floor = _more_conservative(floor, OperatingMode.CAUTIOUS)

    for a in snap.anomaly_flags:
        if a.startswith("runtime_integrity"):
            reasons.append(a)
            severity = max(severity, 93)
            floor = _more_conservative(floor, OperatingMode.HALTED)

    triggered = len(reasons) > 0
    return BrakeEvaluation(
        triggered=triggered,
        recommended_floor=floor,
        reasons=reasons,
        severity=severity,
    )


def mode_size_multiplier(mode: OperatingMode, cfg: OperatingModeConfig) -> Tuple[float, bool]:
    """Returns (multiplier, allow_new_trades)."""
    if mode == OperatingMode.HALTED:
        return 0.0, False
    if mode == OperatingMode.DEFENSIVE:
        return 0.25, True
    if mode == OperatingMode.CAUTIOUS:
        return 0.55, True
    if mode == OperatingMode.NORMAL:
        return 1.0, True
    if mode == OperatingMode.CONFIDENT:
        return min(cfg.confident_size_multiplier, 1.25), True
    return min(cfg.aggressive_confirmed_size_multiplier, 1.4), True
