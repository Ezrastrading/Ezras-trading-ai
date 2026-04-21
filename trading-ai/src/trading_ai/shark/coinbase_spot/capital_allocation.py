"""Gate A / Gate B notional split helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GateAllocationSplit:
    gate_a: float
    gate_b: float
    gate_a_majors: float
    gate_a_other: float


def compute_gate_allocation_split(
    *,
    gate_a_share: float | None = None,
    gate_b_share: float | None = None,
) -> GateAllocationSplit:
    if gate_a_share is not None and gate_b_share is not None:
        ga, gb = float(gate_a_share), float(gate_b_share)
    else:
        ga = float(os.environ.get("AVENUE_A_GATE_A_QUOTE_SHARE", "0.5"))
        gb = float(os.environ.get("AVENUE_A_GATE_B_QUOTE_SHARE", str(max(0.0, 1.0 - ga))))
    if ga + gb > 1.0 + 1e-9:
        s = ga + gb
        ga, gb = ga / s, gb / s
    majors = float(os.environ.get("GATE_A_MAJORS_SHARE", "0.55"))
    other = float(os.environ.get("GATE_A_OTHER_SHARE", str(max(0.0, 1.0 - majors))))
    if majors + other > 1.0 + 1e-9:
        s = majors + other
        majors, other = majors / s, other / s
    return GateAllocationSplit(gate_a=ga, gate_b=gb, gate_a_majors=majors, gate_a_other=other)


def idle_loan_unused_gate_quota_to_other_allowed() -> bool:
    return os.environ.get("IDLE_LOAN_GATE_QUOTA", "").strip().lower() in ("1", "true", "yes")


def gate_b_position_budgets_usd(total_capital_usd: float, *, max_positions: int = 4, regime_multiplier: float = 1.0) -> dict:
    pool = float(total_capital_usd) * 0.5 * float(regime_multiplier)
    per = pool / max(1, int(max_positions))
    return {
        "gate_b_pool_usd": pool,
        "per_position_budget_usd": per,
        "max_positions": int(max_positions),
    }
