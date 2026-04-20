"""Code-level responsibility contracts — intelligence roles, not execution grants."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class AvenueMasterContract(Protocol):
    """Avenue master: venue mechanics, aggregation, teaching — no live promotion authority."""

    def summarize_avenue_priorities_for_ezra(self) -> Dict[str, Any]: ...

    def list_gate_managers(self) -> List[str]: ...

    def propose_research_gate(self, thesis: str, avenue_id: str) -> Dict[str, Any]: ...


@runtime_checkable
class GateManagerContract(Protocol):
    """Gate manager: one gate end-to-end truth shaping — recommendations are advisory."""

    def pass_fail_truth_for_promotion_step(self) -> Dict[str, Any]: ...

    def coordinate_workers(self) -> List[str]: ...


@runtime_checkable
class GateWorkerContract(Protocol):
    """Worker: single narrow job; structured output only."""

    def structured_job_output(self) -> Dict[str, Any]: ...


AVENUE_MASTER_RESPONSIBILITIES: tuple[str, ...] = (
    "learn_avenue_mechanics",
    "aggregate_gate_intelligence",
    "compare_gates",
    "identify_missing_gates",
    "identify_overfit_or_weak_gates",
    "propose_research_for_new_gates",
    "teach_and_support_gate_managers",
    "summarize_avenue_priorities_to_ezra",
)

GATE_MANAGER_RESPONSIBILITIES: tuple[str, ...] = (
    "master_one_gate_end_to_end",
    "know_strategy_execution_shape_edge_assumptions_constraints_failure_modes",
    "coordinate_worker_bots",
    "collect_evidence",
    "request_tuning_or_research",
    "produce_pass_fail_truth_for_promotion_steps",
)

WORKER_RESPONSIBILITIES: tuple[str, ...] = (
    "single_narrow_job",
    "no_broad_authority",
    "structured_outputs_only",
    "report_upward_only",
)


def avenue_master_contract_dict() -> Dict[str, Any]:
    return {
        "role": "avenue_master",
        "responsibilities": list(AVENUE_MASTER_RESPONSIBILITIES),
        "forbidden": [
            "self_grant_live_permissions",
            "bypass_promotion_ladder",
            "instant_autonomous_live",
        ],
    }


def gate_manager_contract_dict() -> Dict[str, Any]:
    return {
        "role": "gate_manager",
        "responsibilities": list(GATE_MANAGER_RESPONSIBILITIES),
        "forbidden": [
            "self_grant_live_permissions",
            "treat_advisory_as_proof",
        ],
    }


def worker_contract_dict() -> Dict[str, Any]:
    return {
        "role": "gate_worker",
        "responsibilities": list(WORKER_RESPONSIBILITIES),
        "forbidden": ["venue_orders", "promotion_authority", "runtime_switch"],
    }
