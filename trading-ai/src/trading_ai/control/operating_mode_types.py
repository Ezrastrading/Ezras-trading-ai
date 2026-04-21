"""Explicit system-wide operating modes — persistent, inspectable, logged."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class OperatingMode(str, Enum):
    HALTED = "halted"
    DEFENSIVE = "defensive"
    CAUTIOUS = "cautious"
    NORMAL = "normal"
    CONFIDENT = "confident"
    AGGRESSIVE_CONFIRMED = "aggressive_confirmed"


# Recovery / escalation order (low → high aggression)
MODE_ORDER: List[OperatingMode] = [
    OperatingMode.HALTED,
    OperatingMode.DEFENSIVE,
    OperatingMode.CAUTIOUS,
    OperatingMode.NORMAL,
    OperatingMode.CONFIDENT,
    OperatingMode.AGGRESSIVE_CONFIRMED,
]


def mode_index(m: OperatingMode) -> int:
    return MODE_ORDER.index(m)


@dataclass
class OperatingModeConfig:
    """Caps and thresholds — env-overridable via :func:`load_operating_mode_config_from_env`."""

    # Emergency brake — loss streak (system-wide; per-gate keys can extend later)
    max_consecutive_losses: int = 5
    max_loss_rate_last_n_trades: float = 0.65
    loss_rate_window_n: int = 20
    max_rolling_drawdown_pct: float = 0.12
    negative_expectancy_window_n: int = 25

    # Execution / market
    slippage_health_min_for_scale: float = 0.45
    liquidity_health_min_for_scale: float = 0.4
    execution_health_min: float = 0.35

    # Confidence scaling (NOT martingale — multipliers apply to *allowed* base size only)
    confident_size_multiplier: float = 1.15
    aggressive_confirmed_size_multiplier: float = 1.35
    max_capital_fraction_per_gate: float = 0.55
    max_capital_fraction_per_edge: float = 0.25
    max_daily_mode_step_up: int = 1
    min_sample_for_confident_mode: int = 30
    min_sample_for_aggressive_confirmed_mode: int = 50

    # Recovery
    min_trades_at_defensive_to_step: int = 8
    min_positive_expectancy_edge: float = 0.0
    recovery_cooldown_sec_after_halt: float = 3600.0

    # Anomaly counts before halt
    max_reconciliation_mismatches_24h: int = 2
    max_governance_blocks_24h: int = 8


@dataclass
class OperatingSnapshot:
    """
    Inputs for one evaluation cycle — caller fills from feeds / journals.

    ``gate_a_expectancy_20`` / ``gate_b_expectancy_20`` are rolling expectancy from **gate-scoped
    production** trade rows (not blended global ``last_n_trade_pnls``).

    ``adaptive_scope_metadata`` documents which scope fed the brake inputs (honesty).
    """

    consecutive_losses: int = 0
    last_n_trade_pnls: List[float] = field(default_factory=list)
    rolling_equity_high: float = 0.0
    current_equity: float = 0.0
    slippage_health: float = 1.0
    liquidity_health: float = 1.0
    execution_health: float = 1.0
    anomaly_flags: List[str] = field(default_factory=list)
    reconciliation_failures_24h: int = 0
    databank_failures_24h: int = 0
    governance_blocks_24h: int = 0
    blocked_orders_streak: int = 0
    gate_a_expectancy_20: Optional[float] = None
    gate_b_expectancy_20: Optional[float] = None
    market_regime: str = "neutral"
    market_chop_score: float = 0.0
    time_since_last_evaluation_sec: float = 60.0
    adaptive_scope_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OperatingOutcome:
    mode: OperatingMode
    prior_mode: OperatingMode
    mode_change_reasons: List[str]
    emergency_brake_triggered: bool
    size_multiplier_effective: float
    allow_new_trades: bool
    diagnosis: Dict[str, Any]
    report: Dict[str, Any]
    critical_alerts: List[str]
