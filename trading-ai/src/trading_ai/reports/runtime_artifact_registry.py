"""
Registry of runtime control/report artifacts — dependency hints, categories, writer ids.

Used by :mod:`trading_ai.reports.runtime_artifact_refresh_manager` for staleness and ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

Category = Literal[
    "control_truth",
    "activation_truth",
    "advisory_only",
    "operator_report",
    "loop_truth",
    "lessons_truth",
    "live_switch_authority",
    "refresh_meta",
]
TruthLevel = Literal["authoritative", "supporting", "advisory"]


def _deps_live_enablement(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "system_execution_lock.json",
        root / "execution_proof" / "live_execution_validation.json",
    ]


def _deps_system_lock(root: Path) -> List[Path]:
    return [root / "data" / "control" / "system_execution_lock.json"]


def _deps_gate_b_control_bundle(root: Path) -> List[Path]:
    return [
        root / "execution_proof" / "gate_b_live_execution_validation.json",
        root / "data" / "control" / "gate_b_validation.json",
        root / "data" / "control" / "operating_mode_state.json",
        root / "data" / "control" / "operating_mode_state_gate_b.json",
        root / "data" / "control" / "adaptive_live_proof.json",
        root / "data" / "databank" / "trade_events.jsonl",
    ]


def _deps_lessons(root: Path) -> List[Path]:
    try:
        from trading_ai.shark.lessons import LESSONS_FILE

        return [Path(LESSONS_FILE)]
    except Exception:
        return []


def _deps_gate_b_loop(root: Path) -> List[Path]:
    return [root / "data" / "control" / "gate_b_last_production_tick.json"]


def _deps_gate_b_final_go_live(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "gate_b_live_status.json",
        root / "data" / "control" / "gate_b_scope_contamination_audit.json",
        root / "data" / "control" / "gate_b_global_halt_truth.json",
        root / "data" / "control" / "lessons_runtime_truth.json",
        root / "data" / "control" / "gate_b_loop_truth.json",
    ]


def _deps_gate_b_activation(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "gate_b_final_go_live_truth.json",
        root / "data" / "control" / "gate_b_scope_contamination_audit.json",
        root / "data" / "control" / "gate_b_global_halt_truth.json",
        root / "data" / "control" / "gate_b_loop_truth.json",
        root / "data" / "control" / "lessons_runtime_truth.json",
    ]


def _deps_daemon_readiness(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "daemon_live_switch_authority.json",
        root / "data" / "control" / "system_execution_lock.json",
        root / "data" / "control" / "gate_b_global_halt_truth.json",
    ]


def _deps_avenue_a_autonomous_runtime(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "universal_execution_loop_proof.json",
        root / "data" / "control" / "avenue_a_daemon_state.json",
        root / "data" / "control" / "runtime_runner_daemon_verification.json",
    ]


def _deps_avenue_a_daemon_support_bundle(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "universal_execution_loop_proof.json",
        root / "execution_proof" / "live_execution_validation.json",
        root / "data" / "control" / "avenue_a_daemon_state.json",
        root / "data" / "control" / "gate_b_last_production_tick.json",
    ]


def _deps_avenue_a_active_stack_truth(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "avenue_a_daemon_live_truth.json",
        root / "execution_proof" / "live_execution_validation.json",
        root / "data" / "control" / "gate_b_last_production_tick.json",
        root / "data" / "control" / "runtime_artifact_refresh_truth.json",
        root / "data" / "control" / "ceo_session_truth.json",
        root / "data" / "control" / "lessons_runtime_truth.json",
    ]


def _deps_avenue_a_bot_hierarchy_truth(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "runtime_runner_last_cycle.json",
        root / "data" / "control" / "avenue_a_active_stack_truth.json",
        root / "data" / "control" / "avenue_a_daemon_live_truth.json",
        root / "execution_proof" / "live_execution_validation.json",
        root / "execution_proof" / "gate_b_live_execution_validation.json",
        root / "state" / "post_trade_manifest.json",
        root / "data" / "control" / "lessons_runtime_truth.json",
        root / "data" / "control" / "lessons_runtime_effect.json",
        root / "data" / "control" / "ceo_session_truth.json",
        root / "shark" / "memory" / "global" / "review_packet_latest.json",
    ]


def _deps_avenue_a_coordination_truth(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "runtime_runner_last_cycle.json",
        root / "execution_proof" / "live_execution_validation.json",
        root / "execution_proof" / "gate_b_live_execution_validation.json",
        root / "state" / "post_trade_manifest.json",
        root / "data" / "control" / "universal_execution_loop_proof.json",
        root / "data" / "control" / "rebuy_runtime_truth.json",
        root / "data" / "control" / "lessons_runtime_truth.json",
        root / "data" / "control" / "lessons_runtime_effect.json",
        root / "data" / "control" / "ceo_session_truth.json",
        root / "shark" / "memory" / "global" / "review_packet_latest.json",
    ]


def _deps_morning_review_readiness_truth(root: Path) -> List[Path]:
    """
    NOTE: This must be defined **before** REGISTRY is constructed.
    (A later re-definition is OK, but the initial name must exist.)
    """
    return [
        root / "shark" / "memory" / "global" / "review_packet_latest.json",
        root / "data" / "control" / "lessons_runtime_truth.json",
        root / "data" / "control" / "ceo_session_truth.json",
        root / "data" / "control" / "universal_execution_loop_proof.json",
        root / "data" / "control" / "avenue_a_coordination_truth.json",
    ]


def _deps_morning_review_readiness_truth__deprecated_duplicate(root: Path) -> List[Path]:
    """
    Deprecated duplicate kept to preserve older imports during refactors.
    Do not use; real definition is earlier in this module.
    """
    return _deps_morning_review_readiness_truth(root)


def _deps_runtime_runner_daemon_verification(root: Path) -> List[Path]:
    return [
        root / "data" / "control" / "daemon_verification_matrix.json",
    ]


def _deps_avenue_a_bot_hierarchy_truth(root: Path) -> List[Path]:
    """
    NOTE: This must be defined **before** REGISTRY is constructed.
    (A later re-definition is OK, but the initial name must exist.)
    """
    return [
        root / "data" / "control" / "avenue_a_active_stack_truth.json",
        root / "data" / "control" / "avenue_a_daemon_live_truth.json",
        root / "execution_proof" / "live_execution_validation.json",
    ]


@dataclass(frozen=True)
class ArtifactSpec:
    id: str
    writer: str  # "module:function"
    dependency_paths: Callable[[Path], Sequence[Path]]
    category: Category
    truth_level: TruthLevel
    blocking_importance: int  # higher = more critical for safety
    primary_output_json: Optional[str] = None  # relative to runtime root
    notes: str = ""


REGISTRY: List[ArtifactSpec] = [
    ArtifactSpec(
        id="live_enablement_truth",
        writer="trading_ai.reports.live_enablement_truth:write_live_enablement_truth",
        dependency_paths=_deps_live_enablement,
        category="control_truth",
        truth_level="supporting",
        blocking_importance=40,
        primary_output_json="data/control/live_enablement_truth.json",
        notes="Env/credential snapshot — not Gate B switch authority alone.",
    ),
    ArtifactSpec(
        id="final_system_lock_status",
        writer="trading_ai.reports.gate_parity_reports:write_final_system_lock_status",
        dependency_paths=_deps_system_lock,
        category="control_truth",
        truth_level="supporting",
        blocking_importance=50,
        primary_output_json="data/control/final_system_lock_status.json",
    ),
    ArtifactSpec(
        id="gate_b_control_bundle",
        writer="trading_ai.reports.gate_b_control_truth:write_gate_b_truth_artifacts",
        dependency_paths=_deps_gate_b_control_bundle,
        category="control_truth",
        truth_level="authoritative",
        blocking_importance=90,
        primary_output_json="data/control/gate_b_live_status.json",
        notes="Includes contamination audit + gate_b_global_halt_truth.",
    ),
    ArtifactSpec(
        id="lessons_runtime_truth",
        writer="trading_ai.reports.lessons_runtime_truth:write_lessons_runtime_truth_artifacts",
        dependency_paths=_deps_lessons,
        category="lessons_truth",
        truth_level="advisory",
        blocking_importance=20,
        primary_output_json="data/control/lessons_runtime_truth.json",
    ),
    ArtifactSpec(
        id="gate_b_loop_truth",
        writer="trading_ai.reports.gate_b_loop_truth:write_gate_b_loop_truth_artifacts",
        dependency_paths=_deps_gate_b_loop,
        category="loop_truth",
        truth_level="authoritative",
        blocking_importance=55,
        primary_output_json="data/control/gate_b_loop_truth.json",
    ),
    ArtifactSpec(
        id="gate_b_final_go_live_truth",
        writer="trading_ai.reports.gate_b_final_go_live_truth:write_gate_b_final_go_live_truth",
        dependency_paths=_deps_gate_b_final_go_live,
        category="live_switch_authority",
        truth_level="authoritative",
        blocking_importance=100,
        primary_output_json="data/control/gate_b_final_go_live_truth.json",
    ),
    ArtifactSpec(
        id="gate_b_final_activation_bundle",
        writer="trading_ai.reports.gate_b_final_activation:write_gate_b_final_activation_artifacts",
        dependency_paths=_deps_gate_b_activation,
        category="activation_truth",
        truth_level="authoritative",
        blocking_importance=95,
        primary_output_json="data/control/gate_b_final_decision_audit.json",
        notes="Decision audit, remaining gaps, activation sequence or blockers.",
    ),
    ArtifactSpec(
        id="daemon_readiness_bundle",
        writer="trading_ai.daemon_testing.daemon_artifact_writers:write_daemon_readiness_bundle",
        dependency_paths=_deps_daemon_readiness,
        category="control_truth",
        truth_level="supporting",
        blocking_importance=60,
        primary_output_json="data/control/autonomous_live_readiness_authority.json",
        notes="Daemon matrix (fake tier) + readiness + final truth — no live orders.",
    ),
    ArtifactSpec(
        id="runtime_runner_daemon_verification",
        writer="trading_ai.daemon_testing.daemon_artifact_writers:write_runtime_runner_daemon_verification",
        dependency_paths=_deps_runtime_runner_daemon_verification,
        category="live_switch_authority",
        truth_level="authoritative",
        blocking_importance=100,
        primary_output_json="data/control/runtime_runner_daemon_verification.json",
        notes="Lock exclusivity and failure-stop verification from daemon matrix coverage — consumed by autonomous runtime proofs.",
    ),
    ArtifactSpec(
        id="avenue_a_autonomous_runtime_bundle",
        writer="trading_ai.orchestration.avenue_a_autonomous_runtime_truth:write_all_avenue_a_autonomous_runtime_artifacts",
        dependency_paths=_deps_avenue_a_autonomous_runtime,
        category="live_switch_authority",
        truth_level="authoritative",
        blocking_importance=105,
        primary_output_json="data/control/avenue_a_autonomous_authority.json",
        notes="Avenue A last-mile runtime proof chain — refreshes when loop/daemon state/verification change.",
    ),
    ArtifactSpec(
        id="avenue_a_daemon_support_bundle",
        writer="trading_ai.orchestration.avenue_a_daemon_artifacts:write_all_avenue_a_daemon_artifacts",
        dependency_paths=_deps_avenue_a_daemon_support_bundle,
        category="operator_report",
        truth_level="supporting",
        blocking_importance=35,
        primary_output_json="data/control/minimal_supervision_contract.json",
        notes="Rebuy truth + supervision contract + CEO freshness + switch booleans (no live permission).",
    ),
    ArtifactSpec(
        id="avenue_a_active_stack_truth",
        writer="trading_ai.orchestration.avenue_a_active_stack_truth:write_avenue_a_active_stack_truth",
        dependency_paths=_deps_avenue_a_active_stack_truth,
        category="operator_report",
        truth_level="authoritative",
        blocking_importance=70,
        primary_output_json="data/control/avenue_a_active_stack_truth.json",
        notes="Canonical Avenue A active stack surface (managers/sub-bots/support/guards) — evidence-first.",
    ),
    ArtifactSpec(
        id="avenue_a_bot_hierarchy_truth",
        writer="trading_ai.orchestration.avenue_a_bot_hierarchy_truth:write_avenue_a_bot_hierarchy_truth",
        dependency_paths=_deps_avenue_a_bot_hierarchy_truth,
        category="operator_report",
        truth_level="authoritative",
        blocking_importance=65,
        primary_output_json="data/control/avenue_a_bot_hierarchy_truth.json",
        notes="Evidence-first bot hierarchy contract for Avenue A (ACTIVE/ADVISORY/DEAD).",
    ),
    ArtifactSpec(
        id="avenue_a_coordination_truth",
        writer="trading_ai.orchestration.avenue_a_coordination_truth:write_avenue_a_coordination_truth",
        dependency_paths=_deps_avenue_a_coordination_truth,
        category="operator_report",
        truth_level="authoritative",
        blocking_importance=65,
        primary_output_json="data/control/avenue_a_coordination_truth.json",
        notes="Evidence-first last-cycle coordination summary across Gate A/Gate B and support bots.",
    ),
    ArtifactSpec(
        id="morning_review_readiness_truth",
        writer="trading_ai.orchestration.morning_review_readiness_truth:write_morning_review_readiness_truth",
        dependency_paths=_deps_morning_review_readiness_truth,
        category="operator_report",
        truth_level="authoritative",
        blocking_importance=60,
        primary_output_json="data/control/morning_review_readiness_truth.json",
        notes="Evidence-first morning review readiness (requires fresh review packet + lessons + loop + coordination).",
    ),
]


def registry_dependency_graph() -> Dict[str, Any]:
    """Human/machine-readable summary for runtime_artifact_refresh_truth.json."""
    return {
        "artifacts": [
            {
                "id": s.id,
                "writer": s.writer,
                "category": s.category,
                "truth_level": s.truth_level,
                "blocking_importance": s.blocking_importance,
                "notes": s.notes,
            }
            for s in REGISTRY
        ],
        "order": [s.id for s in REGISTRY],
        "honesty": (
            "Dependency paths are freshness triggers only — writers re-read full runtime state. "
            "Staleness uses composite fingerprints of these paths, not wall-clock alone."
        ),
    }


def _deps_avenue_a_bot_hierarchy_truth__deprecated_duplicate(root: Path) -> List[Path]:
    """
    Deprecated duplicate kept to preserve older imports during refactors.
    Do not use; real definition is earlier in this module.
    """
    return _deps_avenue_a_bot_hierarchy_truth(root)


def _deps_avenue_a_coordination_truth__deprecated_duplicate(root: Path) -> List[Path]:
    """
    Deprecated duplicate kept to preserve older imports during refactors.
    Do not use; real definition is earlier in this module.
    """
    return _deps_avenue_a_coordination_truth(root)


def _deps_morning_review_readiness_truth__deprecated_duplicate_2(root: Path) -> List[Path]:
    """
    Deprecated duplicate kept to preserve older imports during refactors.
    Do not use; real definition is earlier in this module.
    """
    return _deps_morning_review_readiness_truth(root)
