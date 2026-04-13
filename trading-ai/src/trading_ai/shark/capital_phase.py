"""Capital phase parameters + Kelly fraction scaling — read capital from state on every trade."""

from __future__ import annotations

from dataclasses import dataclass

from trading_ai.shark.models import CapitalPhase, OpportunityTier

# Accelerated compounding — midpoint monthly targets (USD)
ACCELERATION_MONTHLY_TARGET_MID: dict[int, float] = {
    1: 350.0,
    2: 1750.0,
    3: 10000.0,
    4: 70000.0,
    5: 200000.0,
}
YEAR_END_TARGET_DEFAULT: float = 500_000.0


def acceleration_monthly_target_midpoint(month_index_1_based: int) -> float:
    """Return midpoint target for month slot (1..5); beyond 5 uses year-end trajectory."""
    return ACCELERATION_MONTHLY_TARGET_MID.get(
        max(1, min(5, month_index_1_based)),
        YEAR_END_TARGET_DEFAULT,
    )


def monthly_progress_ratio(current_capital: float, monthly_target: float, monthly_start: float) -> float:
    if monthly_target <= monthly_start:
        return 1.0
    return max(0.0, min(1.5, (current_capital - monthly_start) / (monthly_target - monthly_start)))


def year_end_pace_status(current_capital: float, year_end_target: float, months_elapsed: float = 1.0) -> str:
    """Rough on-pace label for memos (not financial advice)."""
    if year_end_target <= 0:
        return "n/a"
    pace = current_capital / year_end_target
    need = months_elapsed / 12.0
    if pace >= need * 0.9:
        return "on_pace_or_ahead"
    if pace >= need * 0.5:
        return "slightly_behind"
    return "behind"


@dataclass(frozen=True)
class PhaseParams:
    min_edge: float
    max_single_position_fraction: float
    kelly_fraction: float  # 0.5 half, 0.25 quarter
    tier_a_phase_multiplier: float


def detect_phase(capital: float) -> CapitalPhase:
    # Phase floors are MINIMUMS. System always pushes to exceed them.
    if capital < 100:       # Floor: $100 — target is beyond this
        return CapitalPhase.PHASE_1
    if capital < 500:       # Floor: $500 — target is beyond this
        return CapitalPhase.PHASE_2
    if capital < 5000:      # Floor: $5,000 — target is beyond this
        return CapitalPhase.PHASE_3
    if capital < 25000:     # Floor: $25,000 — target is beyond this
        return CapitalPhase.PHASE_4
    return CapitalPhase.PHASE_5  # Floor: $25,000+ — no ceiling


def phase_params(phase: CapitalPhase) -> PhaseParams:
    if phase == CapitalPhase.PHASE_1:
        return PhaseParams(
            min_edge=0.07,
            max_single_position_fraction=0.16,
            kelly_fraction=0.5,
            tier_a_phase_multiplier=1.3,
        )
    if phase == CapitalPhase.PHASE_2:
        return PhaseParams(
            min_edge=0.06,
            max_single_position_fraction=0.12,
            kelly_fraction=0.5,
            tier_a_phase_multiplier=1.15,
        )
    if phase == CapitalPhase.PHASE_3:
        return PhaseParams(
            min_edge=0.055,
            max_single_position_fraction=0.10,
            kelly_fraction=0.5,
            tier_a_phase_multiplier=1.1,
        )
    if phase == CapitalPhase.PHASE_4:
        return PhaseParams(
            min_edge=0.05,
            max_single_position_fraction=0.08,
            kelly_fraction=0.25,
            tier_a_phase_multiplier=1.0,
        )
    return PhaseParams(
        min_edge=0.05,
        max_single_position_fraction=0.05,
        kelly_fraction=0.25,
        tier_a_phase_multiplier=1.0,
    )


def tier_multiplier(tier: OpportunityTier) -> float:
    if tier == OpportunityTier.TIER_A:
        return 1.3
    if tier == OpportunityTier.TIER_B:
        return 1.0
    if tier == OpportunityTier.TIER_C:
        return 0.7
    return 0.0


def effective_kelly_base(
    *,
    phase: CapitalPhase,
    tier: OpportunityTier,
    gap_exploitation_mode: bool,
) -> float:
    pp = phase_params(phase)
    if gap_exploitation_mode:
        return 0.80
    return pp.kelly_fraction


def phase_tier_combined_multiplier(phase: CapitalPhase, tier: OpportunityTier) -> float:
    pp = phase_params(phase)
    tm = tier_multiplier(tier)
    extra = pp.tier_a_phase_multiplier if tier == OpportunityTier.TIER_A else 1.0
    return tm * extra
