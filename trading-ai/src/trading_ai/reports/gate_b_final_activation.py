"""
Gate B final go-live decision, remaining gaps (classified), and operator activation or blockers.

All answers are derived from on-disk artifacts and code truth — no wording inflation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.reports.runtime_remaining_gaps import (
    build_runtime_remaining_gaps_final,
    write_runtime_remaining_gaps_final,
)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _artifact_freshness_note(ctrl: Path) -> Dict[str, Any]:
    """Best-effort mtime age for operator awareness — advisory only."""
    paths = [
        ctrl / "gate_b_final_go_live_truth.json",
        ctrl / "gate_b_live_status.json",
        ctrl / "gate_b_last_production_tick.json",
    ]
    out: Dict[str, Any] = {}
    for p in paths:
        if p.is_file():
            try:
                age_s = max(0.0, datetime.now(timezone.utc).timestamp() - p.stat().st_mtime)
                out[p.name] = {"exists": True, "age_seconds_approx": int(age_s)}
            except OSError:
                out[p.name] = {"exists": True, "age_seconds_approx": None}
        else:
            out[p.name] = {"exists": False}
    return out


def build_gate_b_final_decision_audit(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Authoritative answers to Section 1 questions — reconciles existing control artifacts.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"

    live = _read_json(ctrl / "gate_b_live_status.json") or {}
    contam = _read_json(ctrl / "gate_b_scope_contamination_audit.json") or {}
    op_go = _read_json(ctrl / "gate_b_operator_go_live_status.json") or {}
    loop = _read_json(ctrl / "gate_b_loop_truth.json") or {}
    final = _read_json(ctrl / "gate_b_final_go_live_truth.json") or {}
    gh_file = _read_json(ctrl / "gate_b_global_halt_truth.json") or {}
    lessons = _read_json(ctrl / "lessons_runtime_truth.json") or {}
    adaptive = _read_json(ctrl / "gate_b_adaptive_truth.json") or {}

    ready_orders = bool(live.get("gate_b_ready_for_live_orders"))
    micro_live = bool(live.get("gate_b_live_micro_proven"))
    blocked_gb = bool(contam.get("blocked_by_gate_b_adaptive"))
    blocked_global = bool(contam.get("blocked_by_global_adaptive"))
    can_run_tick_adaptive = bool(contam.get("can_run_gate_b_loop_now"))
    tick_artifact_ok = bool(live.get("gate_b_ready_for_continuous_live_loop"))  # tick json proven (legacy field name)
    in_repo_daemon = bool(loop.get("dedicated_gate_b_scheduler_exists"))
    production_loop_proven = bool(loop.get("production_loop_proven"))

    lessons_gb_ranking = bool(lessons.get("lessons_influence_candidate_ranking_gate_b")) or bool(
        lessons.get("lessons_influence_candidate_ranking")
    )

    # Live orders: path ready per validation record vs conservative switch (global halt).
    live_path_ready = ready_orders and micro_live and not bool(live.get("gate_b_disabled_by_runtime_policy"))
    conservative_switch = bool(final.get("gate_b_can_be_switched_live_now"))
    if not final:
        conservative_switch = bool(micro_live and ready_orders and not blocked_gb and not blocked_global)

    blocked_only_global = bool(blocked_global and not blocked_gb and ready_orders and micro_live)
    gh_primary = gh_file.get("global_halt_primary_classification") or (final.get("gate_b_global_halt_truth") or {}).get(
        "global_halt_primary_classification"
    )

    return {
        "truth_version": "gate_b_final_decision_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "artifacts_consulted": [
            str(ctrl / "gate_b_live_status.json"),
            str(ctrl / "gate_b_scope_contamination_audit.json"),
            str(ctrl / "gate_b_operator_go_live_status.json"),
            str(ctrl / "gate_b_loop_truth.json"),
            str(ctrl / "gate_b_final_go_live_truth.json"),
            str(ctrl / "gate_b_global_halt_truth.json"),
            str(ctrl / "lessons_runtime_truth.json"),
            str(ctrl / "gate_b_adaptive_truth.json"),
        ],
        "Q1_can_gate_b_place_live_coinbase_orders_now": {
            "live_order_path_ready_per_validation_and_policy": live_path_ready,
            "conservative_go_live_switch_allowed": conservative_switch,
            "global_halt_primary_classification": gh_primary,
            "answer_plain": (
                "yes_conservative_go_live_allowed"
                if conservative_switch
                else (
                    "no_global_adaptive_halt_blocks_conservative_go_live"
                    if blocked_global and not blocked_gb and live_path_ready
                    else "no_see_gate_b_activation_blockers_or_remaining_gaps_final"
                )
            ),
            "notes": (
                "gate_b_can_be_switched_live_now is computed in gate_b_final_go_live_truth using gate_b_global_halt_truth "
                "(informational brake + classification). Raw persisted global halt in contamination audit may still read "
                "true while switch-live is allowed if halt is stale or gate-mixture with governance ack. "
                "Live orders use the guarded Coinbase path — not gate-b-tick."
            ),
        },
        "Q2_can_gate_b_run_repeated_production_ticks_now": {
            "command_exists": True,
            "adaptive_and_policy_allow_tick": can_run_tick_adaptive,
            "repeated_tick_ready": bool(can_run_tick_adaptive),
            "answer_plain": "yes_if_adaptive_allows" if can_run_tick_adaptive else "no_adaptive_or_policy_blocks_tick",
            "notes": "Repeated execution = operator or cron invoking `python -m trading_ai.deployment gate-b-tick`.",
        },
        "Q3_can_gate_b_run_continuously_24_7_in_repo_daemon_now": {
            "answer": False,
            "answer_plain": "no",
            "notes": "dedicated_gate_b_scheduler_exists is false in gate_b_loop_truth.json.",
        },
        "Q4_are_lessons_actively_affecting_gate_b_trading_decisions_now": {
            "answer": lessons_gb_ranking,
            "answer_plain": "yes" if lessons_gb_ranking else "no",
            "notes": "Gate B Coinbase gate_b_engine does not load lessons.json per lessons_runtime_truth.json.",
        },
        "Q5_is_gate_b_blocked_by_gate_b_specific_adaptive_state": {
            "answer": blocked_gb,
            "answer_plain": "yes" if blocked_gb else "no",
        },
        "Q6_is_gate_b_blocked_only_by_global_adaptive_state": {
            "answer": blocked_only_global,
            "answer_plain": "yes" if blocked_only_global else "no",
            "global_halted_but_gate_b_mode_not_halted": bool(contam.get("global_halted_but_gate_b_mode_not_halted")),
        },
        "Q7_missing_before_safe_live_activation": {
            "gaps_if_any": final.get("remaining_gaps") or [],
            "artifact_freshness_advisory": _artifact_freshness_note(ctrl),
        },
        "Q8_missing_before_full_autonomous_continuous_production": {
            "in_repo_daemon": not in_repo_daemon,
            "lessons_not_wired_to_gate_b_engine": not lessons_gb_ranking,
            "tick_is_scan_only_orders_elsewhere": True,
            "external_scheduler_required_for_repetition": True,
            "production_tick_artifact_proven_at_least_once": production_loop_proven,
            "notes": (
                "Full autonomous production = scheduled ticks + live order wiring + governance; "
                "not implied by live micro proof alone."
            ),
        },
        "cross_check_operator_go_live_status": op_go,
    }


def build_gate_b_remaining_gaps_final(
    *,
    runtime_root: Optional[Path] = None,
    decision_audit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Backward-compatible alias for :func:`build_runtime_remaining_gaps_final`."""
    return build_runtime_remaining_gaps_final(runtime_root=runtime_root, decision_audit=decision_audit)


def _build_safe_activation_sequence(root: Path, ctrl: Path) -> Dict[str, Any]:
    return {
        "truth_version": "gate_b_safe_activation_sequence_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "1_prerequisite_truth_checks": [
            "gate_b_final_go_live_truth.json → gate_b_can_be_switched_live_now == true",
            "gate_b_live_status.json → gate_b_live_micro_proven == true",
            "gate_b_live_status.json → gate_b_ready_for_live_orders == true",
            "gate_b_scope_contamination_audit.json → blocked_by_gate_b_adaptive == false",
            "gate_b_global_halt_truth.json → authoritative switch-live interpretation (not raw operating_mode_state.json alone)",
            "execution_proof/gate_b_live_execution_validation.json present with FINAL_EXECUTION_PROVEN",
        ],
        "2_exact_env_and_flags": [
            "GATE_B_LIVE_EXECUTION_ENABLED=true (operator shell or service env)",
            "EZRAS_RUNTIME_ROOT set to the runtime tree used for data/control and execution_proof",
            "PYTHONPATH includes repo src/ when invoking python -m trading_ai.deployment",
            "Coinbase API credentials per existing NTE/Coinbase deployment (unchanged from your proven micro path)",
        ],
        "3_exact_command_order": [
            "python3 scripts/write_final_control_artifacts.py   # refresh all truth artifacts",
            "Inspect data/control/gate_b_final_go_live_truth.json",
            "If gate_b_can_be_switched_live_now is true, enable live routing only through your existing guarded Coinbase execution path (not gate-b-tick — tick does not submit orders).",
            "Optional repetition: python -m trading_ai.deployment gate-b-tick  # scan+adaptive+engine only",
        ],
        "4_inspect_immediately_after_activation": [
            "data/control/adaptive_live_proof.json (gate, allow_new_trades, brake)",
            "data/control/operating_mode_state_gate_b.json",
            "Supabase/databank trade rows show gate_id=gate_b where applicable",
        ],
        "5_artifacts_that_must_update": [
            "New trade_events rows with trading_gate gate_b for live orders",
            "adaptive_live_proof.json on adaptive-eval paths",
            "operating_mode_state_gate_b.json if mode transitions",
        ],
        "6_when_to_stop_immediately": [
            "emergency_brake_triggered true in adaptive proof",
            "operating mode halted for gate_b or global per governance",
            "reconciliation or databank health failures per existing locks",
            "unexpected order rejects or venue errors on Coinbase path",
        ],
        "7_what_does_not_count_as_proof": [
            "gate-b-tick success alone (no orders placed)",
            "staged micro_validation_pass without live_venue_micro_validation_pass",
            "READY_FOR_FIRST_20 or Gate A artifacts",
            "lessons.json updates without gate_b_engine wiring",
        ],
    }


def _build_activation_blockers(root: Path, ctrl: Path, final: Dict[str, Any], contam: Dict[str, Any]) -> Dict[str, Any]:
    blockers: List[Dict[str, Any]] = []
    can_switch = bool(final.get("gate_b_can_be_switched_live_now"))
    blocked_global = bool(contam.get("blocked_by_global_adaptive"))
    blocked_gb = bool(contam.get("blocked_by_gate_b_adaptive"))
    if not can_switch:
        why = final.get("if_false_exact_why") or "see gate_b_final_go_live_truth.json remaining_gaps"
        if blocked_global and not blocked_gb:
            blockers.append(
                {
                    "blocker": "global_adaptive_halt_only",
                    "detail": why,
                    "fix": "Recover global operating mode per governance; verify last_mode_diagnosis.json vs gate_b scope.",
                    "next_command": "Inspect data/control/last_mode_diagnosis.json and data/control/operating_mode_state.json",
                }
            )
        elif blocked_gb:
            blockers.append(
                {
                    "blocker": "gate_b_adaptive_halt_or_brake",
                    "detail": why,
                    "fix": "Inspect data/control/last_mode_diagnosis_gate_b.json and operating_mode_state_gate_b.json",
                    "next_command": "python3 scripts/write_final_control_artifacts.py",
                }
            )
        else:
            blockers.append(
                {
                    "blocker": "conservative_go_live_false",
                    "detail": why,
                    "fix": "Resolve items in gate_b_remaining_gaps_final.json",
                    "next_command": "python3 scripts/write_final_control_artifacts.py",
                }
            )
    return {
        "truth_version": "gate_b_activation_blockers_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "gate_b_can_be_switched_live_now": bool(final.get("gate_b_can_be_switched_live_now")),
        "blockers": blockers,
        "notes": "Do not enable live order routing until gate_b_can_be_switched_live_now is true in gate_b_final_go_live_truth.json.",
    }


def write_gate_b_final_activation_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    rep = root / "data" / "reports"
    ctrl.mkdir(parents=True, exist_ok=True)
    rep.mkdir(parents=True, exist_ok=True)

    audit = build_gate_b_final_decision_audit(runtime_root=root)
    gaps_payload = build_runtime_remaining_gaps_final(runtime_root=root, decision_audit=audit)
    write_runtime_remaining_gaps_final(runtime_root=root, payload=gaps_payload)
    final = _read_json(ctrl / "gate_b_final_go_live_truth.json") or {}
    contam = _read_json(ctrl / "gate_b_scope_contamination_audit.json") or {}

    can_switch = bool(final.get("gate_b_can_be_switched_live_now"))

    paths: Dict[str, str] = {}

    def _write(name: str, payload: Dict[str, Any]) -> None:
        p = ctrl / name
        p.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        paths[name] = str(p)
        (ctrl / name.replace(".json", ".txt")).write_text(json.dumps(payload, indent=2)[:16000] + "\n", encoding="utf-8")
        paths[name.replace(".json", ".txt")] = str(ctrl / name.replace(".json", ".txt"))

    _write("gate_b_final_decision_audit.json", audit)

    if can_switch:
        seq = _build_safe_activation_sequence(root, ctrl)
        p_seq = ctrl / "gate_b_safe_activation_sequence.json"
        p_seq.write_text(json.dumps(seq, indent=2) + "\n", encoding="utf-8")
        (ctrl / "gate_b_safe_activation_sequence.txt").write_text(json.dumps(seq, indent=2)[:12000] + "\n", encoding="utf-8")
        paths["gate_b_safe_activation_sequence.json"] = str(p_seq)
        paths["gate_b_safe_activation_sequence.txt"] = str(ctrl / "gate_b_safe_activation_sequence.txt")
        # Remove stale blockers if present
        for stale in ("gate_b_activation_blockers.json", "gate_b_activation_blockers.txt"):
            sp = ctrl / stale
            if sp.is_file():
                try:
                    sp.unlink()
                except OSError:
                    pass
    else:
        blk = _build_activation_blockers(root, ctrl, final, contam)
        p_blk = ctrl / "gate_b_activation_blockers.json"
        p_blk.write_text(json.dumps(blk, indent=2) + "\n", encoding="utf-8")
        (ctrl / "gate_b_activation_blockers.txt").write_text(json.dumps(blk, indent=2)[:12000] + "\n", encoding="utf-8")
        paths["gate_b_activation_blockers.json"] = str(p_blk)
        paths["gate_b_activation_blockers.txt"] = str(ctrl / "gate_b_activation_blockers.txt")
        for stale in ("gate_b_safe_activation_sequence.json", "gate_b_safe_activation_sequence.txt"):
            sp = ctrl / stale
            if sp.is_file():
                try:
                    sp.unlink()
                except OSError:
                    pass

    return {"generated_at": audit["generated_at"], "runtime_root": str(root), "can_switch": can_switch, "paths": paths}

