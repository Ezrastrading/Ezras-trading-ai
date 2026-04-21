from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExecutionGradeResult:
    grade: str
    reasons: list[str]


def _slippage_bps(intended_px: Optional[float], actual_px: Optional[float], *, side: str) -> Optional[float]:
    try:
        ip = float(intended_px) if intended_px is not None else None
        ap = float(actual_px) if actual_px is not None else None
    except (TypeError, ValueError):
        return None
    if not ip or ip <= 0 or not ap or ap <= 0:
        return None
    # For BUY: higher actual is worse. For SELL: lower actual is worse.
    raw = (ap - ip) / ip * 10000.0
    if str(side).strip().upper() == "SELL":
        raw = -raw
    return float(raw)


def grade_execution(
    *,
    side: str,
    intended_execution_mode: str,
    actual_execution_mode: str,
    intended_price: Optional[float],
    actual_fill_price: Optional[float],
    slippage_estimate_usd: Optional[float],
    slippage_actual_usd: Optional[float],
    fees_estimate_usd: Optional[float],
    fees_actual_usd: Optional[float],
) -> ExecutionGradeResult:
    """
    Coarse but deterministic grading:
    - F: mode violated (maker requested but taker used) OR missing actual fill price
    - D/C/B/A: based on slippage vs estimate and fee overrun
    """
    reasons: list[str] = []
    im = (intended_execution_mode or "").strip().lower()
    am = (actual_execution_mode or "").strip().lower()
    if not actual_fill_price or float(actual_fill_price) <= 0:
        return ExecutionGradeResult(grade="F", reasons=["missing_actual_fill_price"])
    if im == "maker" and am == "taker":
        return ExecutionGradeResult(grade="F", reasons=["maker_taker_violation"])

    slip_bps = _slippage_bps(intended_price, actual_fill_price, side=side)
    if slip_bps is not None and slip_bps > 50:
        reasons.append("slippage_bps_high")

    def _ratio(actual: Optional[float], est: Optional[float]) -> Optional[float]:
        try:
            a = float(actual) if actual is not None else None
            e = float(est) if est is not None else None
        except (TypeError, ValueError):
            return None
        if a is None or e is None or e <= 0:
            return None
        return a / e

    slip_ratio = _ratio(slippage_actual_usd, slippage_estimate_usd)
    fee_ratio = _ratio(fees_actual_usd, fees_estimate_usd)

    # Defaults when estimates are absent: cannot award A; settle at C/B based on violations.
    if slip_ratio is None and fee_ratio is None:
        if reasons:
            return ExecutionGradeResult(grade="D", reasons=reasons + ["missing_cost_estimates"])
        return ExecutionGradeResult(grade="C", reasons=["missing_cost_estimates"])

    worst = 1.0
    for r in (slip_ratio, fee_ratio):
        if r is not None:
            worst = max(worst, float(r))

    if worst <= 1.1 and not reasons:
        return ExecutionGradeResult(grade="A", reasons=["within_estimates"])
    if worst <= 1.25 and len(reasons) == 0:
        return ExecutionGradeResult(grade="B", reasons=["minor_overrun"])
    if worst <= 1.6:
        return ExecutionGradeResult(grade="C", reasons=reasons + ["moderate_overrun"])
    if worst <= 2.5:
        return ExecutionGradeResult(grade="D", reasons=reasons + ["large_overrun"])
    return ExecutionGradeResult(grade="F", reasons=reasons + ["cost_overrun_extreme"])

