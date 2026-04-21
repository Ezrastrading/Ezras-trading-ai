"""Final lock layer — constitution, promotion rungs, truth writers, operator visibility, scheduling."""

from trading_ai.global_layer.lock_layer.constitution import OBJECTIVE_HIERARCHY, SYSTEM_CONSTITUTION
from trading_ai.global_layer.lock_layer.cross_bot_memory import MEMORY_SCOPE_RULES
from trading_ai.global_layer.lock_layer.handoff_contracts import validate_handoff_envelope
from trading_ai.global_layer.lock_layer.operator_snapshot import build_operator_snapshot
from trading_ai.global_layer.lock_layer.portfolio_risk import evaluate_global_portfolio_risk
from trading_ai.global_layer.lock_layer.promotion_rung import (
    ExecutionRung,
    assert_no_rung_skip,
    execution_rung_for_promotion_tier,
    sync_execution_rung_on_bot,
)
from trading_ai.global_layer.lock_layer.quality_contract import compute_bot_quality_contract
from trading_ai.global_layer.lock_layer.scheduler_fairness import schedule_bots_fairness
from trading_ai.global_layer.lock_layer.simulation_gate import activation_simulation_required
from trading_ai.global_layer.lock_layer.incidents import record_incident
from trading_ai.global_layer.lock_layer.capital_allocation import suggest_lane_weights
from trading_ai.global_layer.lock_layer.truth_writers import (
    TruthDomain,
    finalize_capital_readiness_truth,
    finalize_promotion_cycle_truth,
    is_canonical_writer,
)

__all__ = [
    "OBJECTIVE_HIERARCHY",
    "SYSTEM_CONSTITUTION",
    "MEMORY_SCOPE_RULES",
    "validate_handoff_envelope",
    "build_operator_snapshot",
    "evaluate_global_portfolio_risk",
    "ExecutionRung",
    "assert_no_rung_skip",
    "execution_rung_for_promotion_tier",
    "sync_execution_rung_on_bot",
    "compute_bot_quality_contract",
    "schedule_bots_fairness",
    "activation_simulation_required",
    "record_incident",
    "TruthDomain",
    "finalize_capital_readiness_truth",
    "finalize_promotion_cycle_truth",
    "is_canonical_writer",
    "suggest_lane_weights",
]
