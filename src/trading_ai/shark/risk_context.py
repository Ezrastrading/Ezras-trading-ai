"""Drawdown + idle-capital adjustments — no clock-based trading windows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskExecutionContext:
    """Aggregates modifiers for min edge and position sizing."""

    drawdown_from_peak: float  # 0..1
    drawdown_over_25pct: bool
    drawdown_over_40pct: bool
    idle_capital_over_6h: bool
    position_size_multiplier: float  # 1.0 or 0.5 when dd>25%
    effective_min_edge: float


def compute_drawdown_fraction(current_capital: float, peak_capital: float) -> float:
    if peak_capital <= 0:
        return 0.0
    return max(0.0, (peak_capital - current_capital) / peak_capital)


def effective_min_edge(
    base_min_edge: float,
    *,
    idle_capital_widen: bool,
    drawdown_over_25pct: bool,
) -> float:
    """Idle: +15% max on threshold; DD>25%: +20% on threshold (stacked)."""
    m = base_min_edge
    if idle_capital_widen:
        m *= 1.15
    if drawdown_over_25pct:
        m *= 1.20
    return min(m, 0.95)


def position_scale_drawdown(drawdown_over_25pct: bool) -> float:
    return 0.5 if drawdown_over_25pct else 1.0


def build_risk_context(
    *,
    current_capital: float,
    peak_capital: float,
    base_min_edge: float,
    last_trade_unix: float | None,
    now_unix: float,
    idle_hours_threshold: float = 6.0,
    log_idle_widen: bool = True,
) -> RiskExecutionContext:
    dd = compute_drawdown_fraction(current_capital, peak_capital)
    over_25 = dd > 0.25
    over_40 = dd > 0.40
    idle = False
    if last_trade_unix is not None:
        idle = (now_unix - last_trade_unix) >= idle_hours_threshold * 3600.0
    emin = effective_min_edge(base_min_edge, idle_capital_widen=idle, drawdown_over_25pct=over_25)
    if log_idle_widen and idle:
        from trading_ai.shark.state import IDLE

        IDLE.log_idle("edge_threshold_widen_15pct_max", {"base_min_edge": base_min_edge, "effective": emin})
    ps = position_scale_drawdown(over_25)
    return RiskExecutionContext(
        drawdown_from_peak=dd,
        drawdown_over_25pct=over_25,
        drawdown_over_40pct=over_40,
        idle_capital_over_6h=idle,
        position_size_multiplier=ps,
        effective_min_edge=emin,
    )


def check_drawdown_after_resolution() -> None:
    """Re-evaluate peak/drawdown rules after capital changes."""
    from trading_ai.shark.state_store import load_capital, save_capital

    rec = load_capital()
    save_capital(rec)
