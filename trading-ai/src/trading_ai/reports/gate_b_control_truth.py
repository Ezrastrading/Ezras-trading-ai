"""
Gate B operator truth bundle — adaptive scope, enablement, go-live, and contamination audit.

Honest labels: Gate B can be live-validated while a **global** brake still blocks; both are surfaced.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.control.adaptive_scope import (
    audit_trade_event_row_stats,
    default_production_pnl_only,
    diagnosis_artifact_path_for_key,
    operating_mode_state_path_for_key,
)
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _txt_lines(title: str, rows: Dict[str, Any]) -> str:
    lines = [title, f"Generated: {datetime.now(timezone.utc).isoformat()}", ""]
    for k, v in rows.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


def write_gate_b_truth_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    rev = root / "data" / "review"
    rep = root / "data" / "reports"
    for d in (ctrl, rev, rep):
        d.mkdir(parents=True, exist_ok=True)

    gb = gate_b_live_status_report()
    prod_pnl_only = default_production_pnl_only()
    row_stats = audit_trade_event_row_stats(production_only=prod_pnl_only)
    gb_proof = root / "execution_proof" / "gate_b_live_execution_validation.json"
    gb_proof_raw = _load_json(gb_proof)
    aos_gb = _load_json(operating_mode_state_path_for_key("gate_b"))
    diag_gb = _load_json(diagnosis_artifact_path_for_key("gate_b"))
    aos_global = _load_json(operating_mode_state_path_for_key("global"))
    adaptive_live = _load_json(root / "data" / "control" / "adaptive_live_proof.json")

    live_validated = bool(
        gb_proof_raw
        and gb_proof_raw.get("FINAL_EXECUTION_PROVEN") is True
        and (gb_proof_raw.get("gate_b_order_verified") is True or gb_proof_raw.get("coinbase_order_verified") is True)
    )
    gb_mode = str((aos_gb or {}).get("mode") or "unknown")
    global_mode = str((aos_global or {}).get("mode") or "unknown")
    gb_allow = True
    gb_brake = False
    if isinstance(adaptive_live, dict) and str(adaptive_live.get("gate") or "").lower() == "gate_b":
        gb_allow = bool(adaptive_live.get("allow_new_trades", True))
        gb_brake = bool(adaptive_live.get("emergency_brake_triggered"))

    global_adaptive_halted = global_mode == "halted"
    gate_b_adaptive_halted = gb_mode == "halted" or gb_brake
    blocked_by_global = bool(global_adaptive_halted)
    blocked_by_gate_b = bool(gate_b_adaptive_halted)

    diag_global = _load_json(diagnosis_artifact_path_for_key("global"))
    if gate_b_adaptive_halted and gb_mode == "halted":
        brake_scope = "gate_b"
    elif global_adaptive_halted:
        brake_scope = "global"
    else:
        brake_scope = "none"

    ready_orders = bool(gb.get("gate_b_ready_for_live"))
    policy_ok = not bool(gb.get("gate_b_disabled_by_runtime_policy"))
    can_tick_adaptive = ready_orders and policy_ok and gb_mode != "halted" and not gb_brake

    brake_reasons: list[str] = []
    if not ready_orders:
        brake_reasons.append("gate_b_ready_for_live_orders_false")
    if not policy_ok:
        brake_reasons.append("gate_b_disabled_by_runtime_policy")
    if gb_mode == "halted":
        brake_reasons.append("gate_b_operating_mode_halted")
    if gb_brake:
        brake_reasons.append("gate_b_emergency_brake_in_last_adaptive_live_proof")
    if global_adaptive_halted:
        brake_reasons.append("global_operating_mode_halted_org_governance_review")

    contamination_audit = {
        "evaluation_scope_used": "gate_b",
        "evaluation_scope_used_for_gate_b_production_adaptive": "gate_b",
        "production_pnl_only": prod_pnl_only,
        "production_pnl_only_default": prod_pnl_only,
        "validation_rows_excluded_count": row_stats.get("validation_or_nonproduction_rows_excluded"),
        "gate_a_rows_seen_count": row_stats.get("gate_a_rows_seen_count"),
        "gate_b_rows_seen_count": row_stats.get("gate_b_rows_seen_count"),
        "global_rows_seen_count": row_stats.get("global_production_rows_seen_count"),
        "raw_trade_event_rows": row_stats.get("raw_trade_event_rows"),
        "adaptive_scope_separation": (
            "Emergency brake inputs for Gate B production evaluation use evaluation_scope=gate_b with "
            "production_pnl_only; validation strategy_ids (live_execution_validation, gate_b_live_micro_validation) "
            "and non-production rows are excluded by default. Validation adaptive eval uses persist_adaptive_state=false."
        ),
        "prior_contamination_class": "global_last_n_trade_pnls_mixed_all_gates_and_validation_rows",
        "remediation": "Scoped PnL filters + per-state-key persistence + validation non-persist",
        "blocked_by_global_adaptive": blocked_by_global,
        "blocked_by_gate_b_adaptive": blocked_by_gate_b,
        "global_operating_mode": global_mode,
        "gate_b_operating_mode": gb_mode,
        "global_halted_but_gate_b_mode_not_halted": bool(global_adaptive_halted and gb_mode != "halted"),
        "exact_scope_that_triggered_brake": brake_scope,
        "exact_brake_reason_if_false": (
            "; ".join(brake_reasons)
            if not can_tick_adaptive
            else None
        ),
        "can_run_gate_b_loop_now": bool(can_tick_adaptive),
        "can_run_gate_b_loop_now_meaning": (
            "True when Gate B live orders are allowed by validation/env, runtime policy allows, and Gate B scoped "
            "adaptive state is not halted / not emergency-braked in last gate_b proof. Global halt is surfaced "
            "separately — confirm org policy before live orders even if this is True."
        ),
    }

    adaptive_truth = {
        "evaluation_scope_used": "gate_b",
        "production_pnl_only": prod_pnl_only,
        "row_stats": row_stats,
        "gate_b_operating_mode_persisted": gb_mode,
        "global_operating_mode_persisted": global_mode,
        "gate_b_adaptive_snapshot_from_last_proof": {
            "allow_new_trades": gb_allow,
            "emergency_brake_triggered": gb_brake,
        }
        if adaptive_live
        else None,
        "last_gate_b_diagnosis_path": str(diagnosis_artifact_path_for_key("gate_b")),
        "diagnosis_excerpt": {k: diag_gb.get(k) for k in ("operator_summary",) if isinstance(diag_gb, dict)},
        "global_diagnosis_excerpt": {k: diag_global.get(k) for k in ("operator_summary",) if isinstance(diag_global, dict)},
    }

    enablement = {
        "gate_b_live_validated": live_validated,
        "gate_b_ready_for_live": gb.get("gate_b_ready_for_live"),
        "gate_b_ready_for_live_orders": gb.get("gate_b_ready_for_live"),
        "gate_b_ready_for_live_semantics": (
            "gate_b_ready_for_live == live_order_ready (validation + live venue micro proof + env); "
            "does not mean 24/7 automation or lessons-wired intelligence — see gate_b_loop_truth / lessons_runtime_truth."
        ),
        "gate_b_live_micro_proven": gb.get("gate_b_live_micro_proven"),
        "operator_env_gate_b": gb.get("gate_b_live_execution_enabled"),
    }

    tick_proof = _load_json(ctrl / "gate_b_last_production_tick.json")
    production_tick_proven = bool(isinstance(tick_proof, dict) and tick_proof.get("tick_ok") is True)

    go_live = {
        "can_run_gate_b_loop_now": bool(can_tick_adaptive),
        "blocked_by_global_adaptive": blocked_by_global,
        "blocked_by_gate_b_adaptive": blocked_by_gate_b,
        "global_adaptive_halted": global_adaptive_halted,
        "gate_b_adaptive_halted": gate_b_adaptive_halted,
        "global_halted_but_gate_b_mode_not_halted": contamination_audit["global_halted_but_gate_b_mode_not_halted"],
        "exact_scope_that_triggered_brake": brake_scope,
        "exact_brake_reason_if_false": contamination_audit["exact_brake_reason_if_false"],
        "production_tick_command_proven": production_tick_proven,
        "continuous_automation_requires_external_scheduler": True,
        "honesty": (
            "Global adaptive halt (persisted global operating mode) is independent of Gate B scoped PnL. "
            "If global is halted but Gate B mode is not, Gate B metrics did not necessarily trigger the global halt — "
            "see global vs gate_b diagnosis artifacts."
        ),
    }

    readiness = {
        **enablement,
        **go_live,
        **{k: v for k, v in contamination_audit.items() if k not in go_live},
        "governance_and_lock": "See live_enablement_truth.json and system_execution_lock.json",
    }

    paths_written: Dict[str, str] = {}
    skipped_sections: List[str] = []
    verbose_failures: List[Dict[str, Any]] = []
    failure_stage: Optional[str] = None
    fallback_used = False

    def _write(name: str, sub: Dict[str, Any]) -> None:
        p = ctrl / name
        p.write_text(json.dumps(sub, indent=2, default=str) + "\n", encoding="utf-8")
        paths_written[name] = str(p)

    def _safe_txt(rel: Path, body: str, *, section: str) -> None:
        nonlocal failure_stage
        try:
            rel.write_text(body, encoding="utf-8")
        except OSError as exc:
            verbose_failures.append({"section": section, "error": type(exc).__name__, "detail": str(exc)[:500]})
            skipped_sections.append(section)
            if failure_stage is None:
                failure_stage = section

    compact_truth = {
        "truth_version": "gate_b_truth_compact_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_validated": live_validated,
        "can_run_gate_b_loop_now": bool(can_tick_adaptive),
        "gate_b_ready_for_live": gb.get("gate_b_ready_for_live"),
        "row_stats_summary": {
            "raw_trade_event_rows": row_stats.get("raw_trade_event_rows"),
            "gate_b_rows_seen_count": row_stats.get("gate_b_rows_seen_count"),
            "gate_a_rows_seen_count": row_stats.get("gate_a_rows_seen_count"),
        },
        "blocked_by_global_adaptive": blocked_by_global,
        "blocked_by_gate_b_adaptive": blocked_by_gate_b,
        "honesty": "Compact slice for fault-tolerant writes — see full JSON siblings for operator detail.",
    }
    compact_write_ok = False
    try:
        (ctrl / "gate_b_truth_compact.json").write_text(
            json.dumps(compact_truth, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        paths_written["gate_b_truth_compact.json"] = str(ctrl / "gate_b_truth_compact.json")
        compact_write_ok = True
    except OSError as exc:
        failure_stage = "gate_b_truth_compact"
        verbose_failures.append({"section": "compact", "error": type(exc).__name__, "detail": str(exc)[:500]})

    _write("gate_b_adaptive_truth.json", adaptive_truth)
    _safe_txt(
        ctrl / "gate_b_adaptive_truth.txt",
        _txt_lines("Gate B adaptive truth", {**adaptive_truth, **enablement}),
        section="gate_b_adaptive_truth.txt",
    )
    paths_written["gate_b_adaptive_truth.txt"] = str(ctrl / "gate_b_adaptive_truth.txt")

    _write("gate_b_live_enablement_truth.json", enablement)
    _safe_txt(
        ctrl / "gate_b_live_enablement_truth.txt",
        _txt_lines("Gate B live enablement", enablement),
        section="gate_b_live_enablement_truth.txt",
    )
    paths_written["gate_b_live_enablement_truth.txt"] = str(ctrl / "gate_b_live_enablement_truth.txt")

    _write("gate_b_operator_go_live_status.json", go_live)
    _safe_txt(ctrl / "gate_b_operator_go_live_status.txt", _txt_lines("Gate B go-live", go_live), section="gate_b_operator_go_live_status.txt")
    paths_written["gate_b_operator_go_live_status.txt"] = str(ctrl / "gate_b_operator_go_live_status.txt")

    gb_live_status_out = {
        **gb,
        "gate_b_ready_for_live_orders": gb.get("gate_b_ready_for_live"),
        "gate_b_ready_for_continuous_live_loop": production_tick_proven,
        "gate_b_ready_for_continuous_live_loop_meaning": (
            "True only when data/control/gate_b_last_production_tick.json records tick_ok from "
            "`python -m trading_ai.deployment gate-b-tick`. Continuous scheduling is still operator-driven "
            "(cron/systemd); no in-repo Gate B-only daemon."
        ),
        "continuous_automation_requires_external_scheduler": True,
    }
    _write("gate_b_live_status.json", gb_live_status_out)
    paths_written["gate_b_live_status.json"] = str(ctrl / "gate_b_live_status.json")

    _write("gate_b_scope_contamination_audit.json", contamination_audit)
    _safe_txt(
        ctrl / "gate_b_scope_contamination_audit.txt",
        _txt_lines("Gate B scope / contamination audit", contamination_audit),
        section="gate_b_scope_contamination_audit.txt",
    )
    paths_written["gate_b_scope_contamination_audit.txt"] = str(ctrl / "gate_b_scope_contamination_audit.txt")

    _write("gate_b_production_readiness_matrix.json", readiness)
    _safe_txt(
        ctrl / "gate_b_production_readiness_matrix.txt",
        _txt_lines("Gate B production readiness matrix", readiness),
        section="gate_b_production_readiness_matrix.txt",
    )
    paths_written["gate_b_production_readiness_matrix.txt"] = str(ctrl / "gate_b_production_readiness_matrix.txt")

    ceo = {
        "gate_b_summary": gb.get("notes"),
        "live_validated": live_validated,
        "go_live_status": go_live,
        "read_daily": str(rep / "gate_b_daily_operator_report.json"),
    }
    try:
        (rev / "gate_b_ceo_daily_review.json").write_text(json.dumps(ceo, indent=2) + "\n", encoding="utf-8")
        paths_written["gate_b_ceo_daily_review.json"] = str(rev / "gate_b_ceo_daily_review.json")
    except OSError as exc:
        verbose_failures.append({"section": "gate_b_ceo_daily_review.json", "error": type(exc).__name__, "detail": str(exc)[:500]})
        skipped_sections.append("gate_b_ceo_daily_review.json")
    _safe_txt(rev / "gate_b_ceo_daily_review.txt", _txt_lines("Gate B CEO daily (excerpt)", ceo), section="gate_b_ceo_daily_review.txt")

    operator_readiness_operator_truth = {
        "truth_version": "gate_b_operator_readiness_compact_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode_label": "ranking_and_advisory_unless_deploy_flags_true",
        "source_quality": {
            "trade_event_row_stats": row_stats,
            "production_pnl_only": prod_pnl_only,
            "gate_b_live_status_report_present": bool(gb),
        },
        "calibration_level": "artifact_gated_not_roi_calibrated",
        "account_size_adjustments": "See position sizing / governance — not implied by this report.",
        "slippage_assumptions": "See execution metrics and shadow compare — not modeled as guaranteed.",
        "selected_universe": "evaluation_scope=gate_b with production row filters; see contamination_audit",
        "excluded_symbols_and_why": contamination_audit.get("adaptive_scope_separation"),
        "deployable_vs_advisory": {
            "gate_b_ranking_or_scoring_only": True,
            "actually_deployable_live_orders": bool(can_tick_adaptive and live_validated and ready_orders),
            "honesty": "True deployability requires org policy + non-global halt + operator enablement — see go_live block.",
        },
        "readiness_matrix_ref": str(ctrl / "gate_b_production_readiness_matrix.json"),
    }
    (rep / "gate_b_operator_readiness_compact.json").write_text(
        json.dumps(operator_readiness_operator_truth, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    paths_written["gate_b_operator_readiness_compact.json"] = str(rep / "gate_b_operator_readiness_compact.json")

    daily_op = {
        "gate_b_status": gb,
        "artifacts": paths_written,
        "operator_readiness_compact_ref": str(rep / "gate_b_operator_readiness_compact.json"),
        "operator_questions_answered": {
            "Q1_live_validated": live_validated,
            "Q2_allowed_gate_b_tick_now_adaptive_ok_not_continuous_daemon": go_live["can_run_gate_b_loop_now"],
            "Q3_brake_if_no": (
                f"global_mode={global_mode}; gate_b_mode={gb_mode}; policy={gb.get('gate_b_disabled_by_runtime_policy')}"
            ),
            "Q4_brake_scope": brake_scope,
            "Q5_command": (
                "Micro proof: python -m trading_ai.deployment gate-b-live-micro | "
                "Production tick (no orders): python -m trading_ai.deployment gate-b-tick"
            ),
            "Q6_daily_file": str(rep / "gate_b_daily_operator_report.json"),
        },
    }
    (rep / "gate_b_daily_operator_report.json").write_text(json.dumps(daily_op, indent=2) + "\n", encoding="utf-8")
    (rep / "gate_b_daily_operator_report.txt").write_text(
        _txt_lines("Gate B daily operator report", daily_op["operator_questions_answered"]),
        encoding="utf-8",
    )
    paths_written["gate_b_daily_operator_report.json"] = str(rep / "gate_b_daily_operator_report.json")

    from trading_ai.reports.gate_b_global_halt_truth import write_gate_b_global_halt_truth_artifacts

    gh_out: Dict[str, Any] = {}
    try:
        gh_out = write_gate_b_global_halt_truth_artifacts(runtime_root=root)
        paths_written["gate_b_global_halt_truth.json"] = gh_out["path"]
    except Exception as exc:
        verbose_failures.append({"section": "gate_b_global_halt_truth", "error": type(exc).__name__, "detail": str(exc)[:500]})
        skipped_sections.append("gate_b_global_halt_truth")
        fallback_used = True
        if failure_stage is None:
            failure_stage = "gate_b_global_halt_truth"

    verbose_write_ok = len(verbose_failures) == 0
    write_report = {
        "truth_version": "gate_b_truth_write_report_v1",
        "compact_write_ok": compact_write_ok,
        "verbose_write_ok": verbose_write_ok,
        "failure_stage": failure_stage,
        "fallback_used": fallback_used,
        "skipped_sections": skipped_sections,
        "verbose_failures": verbose_failures,
        "artifact_json_count": len([k for k in paths_written if k.endswith(".json")]),
        "paths_written_count": len(paths_written),
        "honesty": "If verbose_write_ok is false, JSON siblings may still be complete — inspect verbose_failures.",
    }
    try:
        (ctrl / "gate_b_truth_write_report.json").write_text(
            json.dumps(write_report, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        paths_written["gate_b_truth_write_report.json"] = str(ctrl / "gate_b_truth_write_report.json")
    except OSError:
        pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "paths": paths_written,
        "gate_b_truth_write_report": write_report,
    }
