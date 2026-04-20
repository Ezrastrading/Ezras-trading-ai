"""Mission-to-goal execution state — evidence-based; no performance guarantees."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report
from trading_ai.org_organism.io_utils import append_jsonl, write_json_atomic
from trading_ai.org_organism.paths import (
    avenue_goal_state_path,
    gate_goal_state_path,
    mission_execution_state_path,
    mission_progress_timeline_path,
    tomorrow_best_actions_path,
    today_best_actions_path,
)
from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_operating_mode(
    *,
    sup_ready: bool,
    aut_blockers_n: int,
    gate_b_blockers_n: int,
    experiments_open: int,
) -> str:
    if aut_blockers_n > 8 or gate_b_blockers_n > 6:
        return "capital_protection"
    if experiments_open > 0 and not sup_ready:
        return "validation"
    if sup_ready and aut_blockers_n > 2:
        return "stabilization"
    if sup_ready and aut_blockers_n <= 2:
        return "scaling_prep"
    if not sup_ready:
        return "discovery"
    return "validation"


def _milestones_from_evidence(
    controlled: Dict[str, Any], aut_path: Dict[str, Any]
) -> Tuple[str, List[str]]:
    ra = (controlled.get("rollup_answers") or {}) if isinstance(controlled.get("rollup_answers"), dict) else {}
    next_m = "resolve_top_blocker_with_smallest_proof_surface"
    hints: List[str] = []
    if not ra.get("is_avenue_a_supervised_live_ready"):
        hints.append("complete_supervised_runtime_proof_and_gate_a_strict_ok")
    gb = controlled.get("gate_b") or {}
    if isinstance(gb, dict) and gb.get("gate_b_blockers"):
        hints.append("address_gate_b_blockers_before_scaling_gate_b")
    if not aut_path.get("can_arm_autonomous_now"):
        hints.append("clear_autonomous_blockers_via_truth_chain_not_prompting")
    return next_m, hints[:8]


def build_mission_execution_bundle(*, runtime_root: Path, experiment_open_count: int = 0) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)

    controlled = build_controlled_live_readiness_report(runtime_root=root, write_artifact=False)
    aut_path = build_autonomous_operator_path(runtime_root=root)

    ra = (controlled.get("rollup_answers") or {}) if isinstance(controlled.get("rollup_answers"), dict) else {}
    sup_clear = bool(ra.get("is_avenue_a_supervised_live_ready"))
    gb_ctrl = controlled.get("gate_b") or {}
    gb_blockers = list(gb_ctrl.get("gate_b_blockers_deduped") or []) if isinstance(gb_ctrl, dict) else []
    aut_n = len(list(aut_path.get("active_blockers") or []))

    mode = _classify_operating_mode(
        sup_ready=sup_clear,
        aut_blockers_n=aut_n,
        gate_b_blockers_n=len(gb_blockers),
        experiments_open=max(0, int(experiment_open_count)),
    )

    active_goal = "first_clean_supervised_streak_and_repeatable_cluster"
    progress_note = (
        "Measured progress uses on-disk proofs only — not expected ROI."
        if not sup_clear
        else "Supervised path clearer — next: disciplined micro-sequence + artifact freshness."
    )

    fastest_milestone, mile_hints = _milestones_from_evidence(controlled, aut_path)

    conf_path = 0.35 + 0.05 * (1.0 if sup_clear else 0.0) - 0.02 * min(aut_n, 10)
    conf_path = max(0.05, min(0.85, conf_path))
    evidence_q = "high" if sup_clear and aut_n < 4 else ("medium" if aut_n < 10 else "low")

    today_actions: List[str] = [
        "python -m trading_ai.deployment refresh-runtime-artifacts",
        "python -m trading_ai.deployment controlled-live-readiness",
    ]
    if not sup_clear:
        today_actions.insert(0, "python -m trading_ai.deployment refresh-supervised-daemon-truth-chain")
    blocked: List[str] = []
    for b in list(aut_path.get("active_blockers") or [])[:12]:
        blocked.append(f"autonomous:{b}")
    daemon_en = ad.read_json("data/control/daemon_enable_readiness_after_supervised.json") or {}
    blocked.extend([f"daemon_enable:{x}" for x in (daemon_en.get("exact_blockers") or [])[:8]])

    wasteful: List[str] = [
        "increasing_size_before_gate_a_strict_proof_ok",
        "treating_prompt_output_as_runtime_proof",
        "skipping_supabase_schema_verification_when_sync_expected",
    ]

    mission = {
        "truth_version": "mission_execution_state_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "no_guaranteed_returns": True,
        "current_mission_stage": mode,
        "current_active_goal": active_goal,
        "progress_toward_current_goal": progress_note,
        "fastest_realistic_next_milestone": fastest_milestone,
        "milestone_hints_evidence_based": mile_hints,
        "confidence_in_current_path": round(conf_path, 3),
        "confidence_is_heuristic_not_prediction": True,
        "evidence_quality_label": evidence_q,
        "blocked_actions": blocked[:24],
        "wasteful_actions_to_avoid": wasteful,
        "avenue_A_gate_B_context": "Gate B shares Avenue A capital split; mission pressure is measured separately per gate.",
    }
    write_json_atomic(mission_execution_state_path(root), mission)

    avenue_goals = {
        "truth_version": "avenue_goal_state_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "A": {
            "avenue_id": "A",
            "focus": "coinbase_nte_supervised_then_staged_automation",
            "next_checkpoint": "supervised_live_truth_green_and_micro_validation_streak",
            "blockers_digest": (controlled.get("avenue_a_supervised") or {}).get("supervised_blockers_deduped", [])[:12]
            if isinstance(controlled.get("avenue_a_supervised"), dict)
            else [],
        },
        "B": {"avenue_id": "B", "focus": "kalshi_path_independent", "next_checkpoint": "avenue_specific_proof_chain"},
        "C": {"avenue_id": "C", "focus": "tastytrade_path_independent", "next_checkpoint": "avenue_specific_proof_chain"},
    }
    write_json_atomic(avenue_goal_state_path(root), avenue_goals)

    gate_goals = {
        "truth_version": "gate_goal_state_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "gate_a": {
            "gate_id": "gate_a",
            "goal": "clean_selection_and_strict_proof_for_configured_product",
            "blockers": (controlled.get("gate_a") or {}).get("gate_a_blockers_deduped", [])[:16]
            if isinstance(controlled.get("gate_a"), dict)
            else [],
        },
        "gate_b": {
            "gate_id": "gate_b",
            "goal": "momentum_lane_validation_without_masking_global_brakes",
            "blockers": gb_blockers[:16],
        },
    }
    write_json_atomic(gate_goal_state_path(root), gate_goals)

    tomorrow_actions = [
        "re-run controlled-live-readiness after any env or proof change",
        "append experiment_results for any harness run",
        "review organism_advisory_queue.jsonl for scorecard/waste signals",
    ]
    today_payload = {
        "truth_version": "today_best_actions_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "actions": today_actions,
        "rationale": "Minimize proof surface; refresh truth before capital.",
    }
    tomorrow_payload = {
        "truth_version": "tomorrow_best_actions_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "actions": tomorrow_actions,
    }
    write_json_atomic(today_best_actions_path(root), today_payload)
    write_json_atomic(tomorrow_best_actions_path(root), tomorrow_payload)

    append_jsonl(
        mission_progress_timeline_path(root),
        {
            "ts": _now_iso(),
            "event": "mission_bundle_refresh",
            "mission_stage": mode,
            "supervised_path_clear": sup_clear,
            "autonomous_blocker_count": aut_n,
        },
    )

    return {
        "mission_execution_state": mission,
        "avenue_goal_state": avenue_goals,
        "gate_goal_state": gate_goals,
        "today_best_actions": today_payload,
        "tomorrow_best_actions": tomorrow_payload,
    }
