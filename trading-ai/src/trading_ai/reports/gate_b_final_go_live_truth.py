"""
Authoritative Gate B go-live truth — aggregates artifacts; never inflates readiness.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.reports.gate_b_global_halt_truth import compute_gate_b_can_be_switched_live_now


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def build_gate_b_final_go_live_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"

    live_status = _read_json(ctrl / "gate_b_live_status.json") or {}
    contam = _read_json(ctrl / "gate_b_scope_contamination_audit.json") or {}
    lessons_rt = _read_json(ctrl / "lessons_runtime_truth.json") or {}
    loop = _read_json(ctrl / "gate_b_loop_truth.json") or {}
    op_go = _read_json(ctrl / "gate_b_operator_go_live_status.json") or {}

    micro_live = bool(live_status.get("gate_b_live_micro_proven"))
    micro_staged = bool(live_status.get("gate_b_staged_micro_proven"))
    ready_orders = bool(live_status.get("gate_b_ready_for_live_orders"))
    tick_proven = bool(live_status.get("gate_b_ready_for_continuous_live_loop"))
    loop_proven = bool(loop.get("production_loop_proven"))
    cont_ready = bool(tick_proven and loop_proven)

    blocked_global = bool(contam.get("blocked_by_global_adaptive"))
    blocked_gb = bool(contam.get("blocked_by_gate_b_adaptive"))
    global_but_gb_ok = bool(contam.get("global_halted_but_gate_b_mode_not_halted"))

    lessons_active = bool(lessons_rt.get("lessons_influence_candidate_ranking_gate_b")) or bool(
        lessons_rt.get("lessons_influence_entry_filtering_gate_b")
    ) or bool(lessons_rt.get("lessons_influence_candidate_ranking")) or bool(
        lessons_rt.get("lessons_influence_entry_filtering")
    )

    manual_steps: List[str] = []
    if not tick_proven:
        manual_steps.append(
            "Run once: `python -m trading_ai.deployment gate-b-tick` to emit gate_b_last_production_tick.json "
            "(scan + adaptive + engine; no orders)."
        )
    manual_steps.append(
        "Continuous scheduling is operator-driven: cron/systemd invoking gate-b-tick — no in-repo Gate B daemon."
    )
    if not lessons_active:
        manual_steps.append(
            "Lessons JSON is not consumed by Gate B Coinbase gate_b_engine — see lessons_effect_on_runtime.json."
        )

    repeated_tick_ready = bool(contam.get("can_run_gate_b_loop_now"))
    sem = loop.get("continuous_loop_semantics") if isinstance(loop.get("continuous_loop_semantics"), dict) else {}
    continuous_loop_ready = bool(sem.get("continuous_loop_ready"))
    full_autonomous = bool(sem.get("full_autonomous_production_ready"))

    can_switch, gh_truth = compute_gate_b_can_be_switched_live_now(
        runtime_root=root,
        micro_live=micro_live,
        ready_orders=ready_orders,
        blocked_gb_adaptive=blocked_gb,
        blocked_global_adaptive_raw=blocked_global,
    )

    gaps: List[str] = []
    if not micro_live:
        gaps.append("gate_b_live_micro_proven is false — live Coinbase round-trip proof missing or incomplete")
    if not ready_orders:
        gaps.append("gate_b_ready_for_live_orders is false — env/validation/policy")
    if not tick_proven:
        gaps.append("Production tick not proven — no gate_b_last_production_tick.json with tick_ok")
    if contam.get("can_run_gate_b_loop_now") is False:
        reason = contam.get("exact_brake_reason_if_false")
        if reason:
            gaps.append(f"Adaptive/caution: {reason}")
    if blocked_global and not can_switch:
        gaps.append("Global operating mode halted — confirm governance before any live trading (see gate_b_global_halt_truth.json)")
    elif blocked_global and can_switch:
        gaps.append(
            "advisory_only: persisted operating_mode_state.json may still show global halted — "
            "gate_b_global_halt_truth.json explains decoupled Gate B switch authority"
        )
    if blocked_gb:
        gaps.append("Gate B scoped adaptive halted or emergency brake in last gate_b proof")

    why_not: Optional[str] = None
    if not can_switch:
        parts = list(gaps)
        if global_but_gb_ok and blocked_global:
            parts.append(
                "Note: global halt active while Gate B persisted mode may still be non-halted — see contamination audit."
            )
        if gh_truth.get("exact_do_not_go_live_reason_if_false"):
            parts.append(str(gh_truth["exact_do_not_go_live_reason_if_false"]))
        why_not = "; ".join(parts) if parts else "See gate_b_live_status.json and gate_b_scope_contamination_audit.json"

    gh_m = gh_truth or {}
    operator_ack_present = bool(gh_m.get("operator_governance_ack_present"))
    operator_ack_required = bool(gh_m.get("operator_clearable_blocker"))
    blocked_global_authoritative = bool(gh_m.get("global_halt_is_currently_authoritative"))
    tech_blockers = gh_m.get("technical_blockers_remaining") or []

    return {
        "truth_version": "gate_b_final_go_live_truth_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "gate_b_live_micro_proven": micro_live,
        "gate_b_staged_micro_proven": micro_staged,
        "gate_b_ready_for_live_orders": ready_orders,
        "gate_b_ready_for_continuous_live_loop": cont_ready,
        "semantic_live_micro_proven": micro_live,
        "semantic_live_order_ready": ready_orders,
        "semantic_repeated_tick_ready": repeated_tick_ready,
        "semantic_continuous_loop_ready_in_repo_daemon": bool(continuous_loop_ready),
        "semantic_full_autonomous_production_ready": bool(full_autonomous),
        "gate_b_continuous_tick_proven": tick_proven,
        "gate_b_continuous_automation_requires_external_scheduler": bool(
            live_status.get("continuous_automation_requires_external_scheduler", True)
        ),
        "gate_b_blocked_by_global_state": blocked_global,
        "blocked_by_global_adaptive_raw": blocked_global,
        "blocked_by_global_adaptive_authoritative": blocked_global_authoritative,
        "gate_b_blocked_by_gate_b_state": blocked_gb,
        "blocked_by_gate_b_adaptive": blocked_gb,
        "global_halted_but_gate_b_mode_not_halted": global_but_gb_ok,
        "technical_blockers_remaining": tech_blockers,
        "operator_ack_required": operator_ack_required,
        "operator_ack_present": operator_ack_present,
        "gate_b_lessons_runtime_active": lessons_active,
        "lessons_runtime_intelligence_ready": lessons_active,
        "production_tick_proven": tick_proven,
        "repeated_tick_ready": repeated_tick_ready,
        "continuous_loop_ready_in_repo_daemon": bool(continuous_loop_ready),
        "full_autonomous_continuous_production_ready": bool(full_autonomous),
        "safe_activation_sequence_artifact_path_if_true": str(ctrl / "gate_b_safe_activation_sequence.json"),
        "blockers_artifact_path_if_false": str(ctrl / "gate_b_activation_blockers.json"),
        "gate_b_operator_manual_steps_remaining": manual_steps,
        "gate_b_remaining_gaps_count": len(gaps),
        "gate_b_can_be_switched_live_now": can_switch,
        "gate_b_global_halt_truth": gh_truth,
        "gate_b_switch_live_decouples_raw_global_halt": True,
        "if_false_exact_why": why_not,
        "exact_do_not_go_live_reason_if_false": why_not,
        "remaining_gaps": gaps,
        "manual_steps_remaining": manual_steps,
        "cross_check_operator_go_live_status": op_go,
        "artifacts_consulted": [
            str(ctrl / "gate_b_live_status.json"),
            str(ctrl / "gate_b_scope_contamination_audit.json"),
            str(ctrl / "gate_b_global_halt_truth.json"),
            str(ctrl / "lessons_runtime_truth.json"),
            str(ctrl / "gate_b_loop_truth.json"),
            str(ctrl / "gate_b_adaptive_truth.json"),
            str(ctrl / "gate_b_operator_go_live_status.json"),
        ],
        "exact_command_for_next_step_if_any": (
            "python -m trading_ai.deployment gate-b-tick"
            if not tick_proven
            else "python -m trading_ai.deployment gate-b-live-micro (micro proof refresh) or schedule gate-b-tick"
        ),
    }


def write_gate_b_final_go_live_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_gate_b_final_go_live_truth(runtime_root=root)
    (ctrl / "gate_b_final_go_live_truth.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"truth_version: {payload['truth_version']}",
        f"semantic_live_micro_proven: {payload.get('semantic_live_micro_proven')}",
        f"semantic_live_order_ready: {payload.get('semantic_live_order_ready')}",
        f"semantic_repeated_tick_ready: {payload.get('semantic_repeated_tick_ready')}",
        f"semantic_continuous_loop_ready_in_repo_daemon: {payload.get('semantic_continuous_loop_ready_in_repo_daemon')}",
        f"semantic_full_autonomous_production_ready: {payload.get('semantic_full_autonomous_production_ready')}",
        f"gate_b_live_micro_proven: {payload['gate_b_live_micro_proven']}",
        f"gate_b_staged_micro_proven: {payload['gate_b_staged_micro_proven']}",
        f"gate_b_ready_for_live_orders: {payload['gate_b_ready_for_live_orders']}",
        f"gate_b_ready_for_continuous_live_loop: {payload['gate_b_ready_for_continuous_live_loop']}",
        f"gate_b_blocked_by_global_state: {payload['gate_b_blocked_by_global_state']}",
        f"gate_b_blocked_by_gate_b_state: {payload['gate_b_blocked_by_gate_b_state']}",
        f"gate_b_lessons_runtime_active: {payload['gate_b_lessons_runtime_active']}",
        f"gate_b_remaining_gaps_count: {payload['gate_b_remaining_gaps_count']}",
        f"gate_b_can_be_switched_live_now: {payload['gate_b_can_be_switched_live_now']}",
        f"global_halt_primary_classification: {(payload.get('gate_b_global_halt_truth') or {}).get('global_halt_primary_classification')}",
        f"if_false_exact_why: {payload.get('if_false_exact_why')}",
    ]
    (ctrl / "gate_b_final_go_live_truth.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"generated_at": payload["generated_at"], "path": str(ctrl / "gate_b_final_go_live_truth.json")}
