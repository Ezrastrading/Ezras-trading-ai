"""
Authoritative structured remaining gaps for Gate B — honest classification, evidence, fix paths.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _gap(
    *,
    gid: str,
    title: str,
    classification: str,
    why: str,
    sources: List[str],
    evidence: Dict[str, Any],
    fix: str,
    next_cmd: str,
    auto_clearable: bool,
    operator_ack_clearable: bool,
    ignorable_for_switch: bool,
) -> Dict[str, Any]:
    return {
        "id": gid,
        "title": title,
        "classification": classification,
        "why_it_exists": why,
        "source_artifacts": sources,
        "exact_evidence_fields": evidence,
        "exact_fix_needed": fix,
        "next_command_if_any": next_cmd,
        "auto_clearable": auto_clearable,
        "operator_ack_clearable": operator_ack_clearable,
        "can_be_ignored_for_live_switch": ignorable_for_switch,
    }


def build_runtime_remaining_gaps_final(
    *,
    runtime_root: Optional[Path] = None,
    decision_audit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"

    if decision_audit is None:
        from trading_ai.reports.gate_b_final_activation import build_gate_b_final_decision_audit

        decision_audit = build_gate_b_final_decision_audit(runtime_root=root)

    final = _read_json(ctrl / "gate_b_final_go_live_truth.json") or {}
    contam = _read_json(ctrl / "gate_b_scope_contamination_audit.json") or {}
    loop = _read_json(ctrl / "gate_b_loop_truth.json") or {}
    lessons = _read_json(ctrl / "lessons_runtime_truth.json") or {}
    gh = _read_json(ctrl / "gate_b_global_halt_truth.json") or final.get("gate_b_global_halt_truth") or {}

    can_switch = bool(final.get("gate_b_can_be_switched_live_now"))
    blocked_global_raw = bool(contam.get("blocked_by_global_adaptive"))
    blocked_gb = bool(contam.get("blocked_by_gate_b_adaptive"))
    gh_primary = str(gh.get("global_halt_primary_classification") or "")

    items: List[Dict[str, Any]] = []

    if not bool(loop.get("dedicated_gate_b_scheduler_exists")):
        items.append(
            _gap(
                gid="no_in_repo_always_on_scheduler",
                title="No in-repo Gate B daemon",
                classification="blocks_continuous_automation_only",
                why="Repository does not ship a long-running scheduler for Coinbase Gate B.",
                sources=[str(ctrl / "gate_b_loop_truth.json")],
                evidence={"dedicated_gate_b_scheduler_exists": loop.get("dedicated_gate_b_scheduler_exists")},
                fix="Use cron/systemd to invoke gate-b-tick or ship a future daemon.",
                next_cmd="python -m trading_ai.deployment gate-b-tick",
                auto_clearable=False,
                operator_ack_clearable=False,
                ignorable_for_switch=True,
            )
        )

    lg = bool(lessons.get("lessons_influence_candidate_ranking_gate_b")) or bool(
        lessons.get("lessons_influence_candidate_ranking")
    )
    if not lg:
        items.append(
            _gap(
                gid="lessons_not_in_gate_b_engine",
                title="Lessons not wired into Gate B order path",
                classification="blocks_lessons_runtime_intelligence_only",
                why="gate_b_engine does not read lessons.json for ranking/sizing of live orders.",
                sources=[str(ctrl / "lessons_runtime_truth.json")],
                evidence={"lessons_influence_candidate_ranking_gate_b": False},
                fix="Wire lessons behind a flag + tests, or accept advisory-only lessons.",
                next_cmd="python3 scripts/write_final_control_artifacts.py",
                auto_clearable=False,
                operator_ack_clearable=False,
                ignorable_for_switch=True,
            )
        )

    if blocked_global_raw and not can_switch:
        cls = "blocks_live_orders_now"
        ign = False
        if gh_primary == "STALE_PERSISTED_STATE":
            cls = "advisory_only"
            ign = False
        items.append(
            _gap(
                gid="global_adaptive_halt_or_decoupled_block",
                title="Global halt or switch-live authority denies go-live",
                classification=cls,
                why="Persisted global mode halted and/or interpreted authority blocks conservative switch.",
                sources=[
                    str(ctrl / "gate_b_scope_contamination_audit.json"),
                    str(ctrl / "gate_b_global_halt_truth.json"),
                ],
                evidence={
                    "blocked_by_global_adaptive": blocked_global_raw,
                    "global_halt_primary_classification": gh_primary,
                    "gate_b_can_be_switched_live_now": can_switch,
                },
                fix="Governance recovery or operator ack per gate_b_global_halt_truth; never auto-clear JSON.",
                next_cmd=str(gh.get("exact_next_command") or "python3 scripts/write_final_control_artifacts.py"),
                auto_clearable=False,
                operator_ack_clearable=bool(gh.get("operator_clearable_blocker")),
                ignorable_for_switch=ign,
            )
        )
    elif blocked_global_raw and can_switch:
        items.append(
            _gap(
                gid="global_persisted_halt_advisory",
                title="Global JSON may still read halted while Gate B switch allowed",
                classification="advisory_only",
                why="Raw operating_mode_state.json can remain halted while authoritative switch is true.",
                sources=[str(ctrl / "gate_b_global_halt_truth.json")],
                evidence={"gate_b_can_be_switched_live_now": True, "blocked_by_global_adaptive_raw": True},
                fix="Optionally clear global halt through normal adaptive recovery — not required for Gate B ack path.",
                next_cmd="Inspect data/control/operating_mode_state.json",
                auto_clearable=False,
                operator_ack_clearable=False,
                ignorable_for_switch=True,
            )
        )

    if blocked_gb:
        items.append(
            _gap(
                gid="gate_b_adaptive_halt_or_brake",
                title="Gate B scoped halt or emergency brake",
                classification="blocks_live_orders_now",
                why="Gate B operating mode halted or proof shows emergency brake.",
                sources=[
                    str(ctrl / "gate_b_scope_contamination_audit.json"),
                    str(ctrl / "operating_mode_state_gate_b.json"),
                ],
                evidence={"blocked_by_gate_b_adaptive": True},
                fix="Review last_mode_diagnosis_gate_b.json and recover per runbook.",
                next_cmd="python3 scripts/write_final_control_artifacts.py",
                auto_clearable=False,
                operator_ack_clearable=False,
                ignorable_for_switch=False,
            )
        )

    if not bool(decision_audit.get("Q2_can_gate_b_run_repeated_production_ticks_now", {}).get("repeated_tick_ready")):
        items.append(
            _gap(
                gid="tick_blocked_by_adaptive_or_policy",
                title="Production tick blocked by adaptive/policy",
                classification="blocks_repeated_operator_ticks_only",
                why="can_run_gate_b_loop_now is false in contamination audit.",
                sources=[str(ctrl / "gate_b_scope_contamination_audit.json")],
                evidence={"can_run_gate_b_loop_now": contam.get("can_run_gate_b_loop_now")},
                fix="Resolve exact_brake_reason_if_false in contamination audit.",
                next_cmd="python3 scripts/write_final_control_artifacts.py",
                auto_clearable=False,
                operator_ack_clearable=False,
                ignorable_for_switch=False,
            )
        )

    tick_path = ctrl / "gate_b_last_production_tick.json"
    if not tick_path.is_file():
        items.append(
            _gap(
                gid="no_tick_artifact_yet",
                title="No gate_b_last_production_tick.json",
                classification="advisory_only",
                why="Production tick never wrote a successful artifact.",
                sources=[str(tick_path)],
                evidence={"exists": False},
                fix="Run gate-b-tick once (no orders).",
                next_cmd="python -m trading_ai.deployment gate-b-tick",
                auto_clearable=True,
                operator_ack_clearable=False,
                ignorable_for_switch=True,
            )
        )

    return {
        "truth_version": "gate_b_remaining_gaps_final_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "remaining_gaps_count": len(items),
        "items": items,
        "questions_answered": {
            "Q1_live_coinbase_orders_now": bool(final.get("gate_b_ready_for_live_orders")) and can_switch,
            "Q2_repeated_production_ticks_now": bool(
                decision_audit.get("Q2_can_gate_b_run_repeated_production_ticks_now", {}).get("repeated_tick_ready")
            ),
            "Q3_fully_continuous_autonomous_now": False,
            "Q4_lessons_affect_gate_b_orders": lg,
            "blockers_for_live_switch_tonight": [i["id"] for i in items if not i["can_be_ignored_for_live_switch"]],
            "blockers_repeated_supervised": [i["id"] for i in items if i["classification"] == "blocks_repeated_operator_ticks_only"],
            "blockers_full_autonomous": [i["id"] for i in items if i["classification"] == "blocks_continuous_automation_only"],
            "blockers_lessons_maturity": [i["id"] for i in items if i["classification"] == "blocks_lessons_runtime_intelligence_only"],
            "advisory_only": [i["id"] for i in items if i["classification"] == "advisory_only"],
        },
        "honesty": (
            "Q3 is false: no in-repo daemon. Gap list is evidence-based; can_switch comes from gate_b_final_go_live_truth.json."
        ),
    }


def write_runtime_remaining_gaps_final(
    *,
    runtime_root: Optional[Path] = None,
    payload: Optional[Dict[str, Any]] = None,
    decision_audit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    data = payload or build_runtime_remaining_gaps_final(runtime_root=root, decision_audit=decision_audit)
    (ctrl / "gate_b_remaining_gaps_final.json").write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    (ctrl / "gate_b_remaining_gaps_final.txt").write_text(json.dumps(data, indent=2)[:24000] + "\n", encoding="utf-8")
    return {
        "artifact_name": "gate_b_remaining_gaps_final",
        "path_json": str(ctrl / "gate_b_remaining_gaps_final.json"),
        "path_txt": str(ctrl / "gate_b_remaining_gaps_final.txt"),
        "written": True,
        "truth_level": "authoritative",
    }
