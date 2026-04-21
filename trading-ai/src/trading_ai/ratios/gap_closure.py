"""
Honest gap-closure audit + live status matrix — no fake deployment claims.

Writes:
- data/control/final_gap_closure_audit.{json,txt}
- data/control/honest_live_status_matrix.{json,txt}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _exists(p: Path) -> bool:
    return p.is_file()


def distinction_fields_reference() -> Dict[str, Any]:
    return {
        "code_exists": True,
        "imported": True,
        "invoked": False,
        "artifact_written": False,
        "test_covered": False,
        "local_runtime_proven": False,
        "external_apply_required": False,
        "external_deploy_required": False,
        "externally_deployed_unknown": True,
        "live_behavior_changed": False,
        "live_behavior_proven": False,
        "git_commit_state_unknown_from_runtime": True,
        "deployed_state_unverified": True,
    }


def _distinction_fields_for_subsystem_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Mandatory distinction snapshot per matrix row (section 6)."""
    df = distinction_fields_reference()
    st = (row.get("status") or "").lower()
    df["code_exists"] = True
    df["imported"] = True
    df["artifact_written"] = bool(row.get("artifact_exists_at_audit_time"))
    df["test_covered"] = bool(row.get("test_names_if_any"))
    df["invoked"] = any(
        x in st
        for x in (
            "invoked",
            "live_path",
            "wired_into_live",
            "validation_path",
        )
    )
    df["local_runtime_proven"] = df["artifact_written"] or ("validation_path" in st)
    return df


def _distinction_fields_for_gap_item(it: Dict[str, Any]) -> Dict[str, Any]:
    df = distinction_fields_reference()
    df["code_exists"] = bool(it.get("exists_in_repo"))
    df["imported"] = df["code_exists"]
    df["invoked"] = bool(it.get("invoked_by_validation_path") or it.get("invoked_by_live_path"))
    df["artifact_written"] = bool(it.get("artifact_written_in_runtime"))
    df["test_covered"] = bool(it.get("has_tests"))
    df["local_runtime_proven"] = bool(it.get("artifact_written_in_runtime"))
    return df


def build_honest_live_status_matrix(*, runtime_root: Path) -> Dict[str, Any]:
    ctrl = runtime_root / "data" / "control"
    rev = runtime_root / "data" / "review"
    learn = runtime_root / "data" / "learning"
    dep = runtime_root / "data" / "deployment"

    def row(
        subsystem: str,
        status: str,
        why: str,
        *,
        proof_path: Optional[str] = None,
        entrypoint: Optional[str] = None,
        tests: Optional[List[str]] = None,
        next_level: str = "",
    ) -> Dict[str, Any]:
        return {
            "subsystem": subsystem,
            "status": status,
            "why": why,
            "proof_path_if_any": proof_path,
            "runtime_entrypoint_if_any": entrypoint,
            "test_names_if_any": tests or [],
            "what_would_be_required_for_next_status_level": next_level,
        }

    matrix: List[Dict[str, Any]] = [
        row(
            "Gate A execution",
            "validation_path_invoked",
            "Micro-validation and NTE paths invoke Coinbase; not same as unrestricted prod scale.",
            proof_path=str(dep / "live_validation_streak.json"),
            entrypoint="trading_ai.deployment.live_micro_validation",
            tests=["tests/test_live_micro_validation_*.py"],
            next_level="Operator runs streak clean + governance + proofs → ready_for_first_20",
        ),
        row(
            "Gate B execution",
            "intentionally_disabled",
            "Default GATE_B_LIVE_EXECUTION_ENABLED off unless operator sets env.",
            proof_path=str(ctrl / "gate_b_validation.json"),
            entrypoint="trading_ai.shark.coinbase_spot.gate_b_live_status",
            next_level="Enable flag + validation artifact for STATE_C",
        ),
        row(
            "validation preflight",
            "validation_path_invoked",
            "resolve_validation_product_coherent runs on pre-resolve and validation-products.",
            entrypoint="trading_ai.nte.execution.routing.integration.validation_resolve",
            tests=["tests/test_runtime_coinbase_policy_unified.py", "tests/test_routing_validation_coherent.py"],
            next_level="Always-on credentials + EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS for artifacts",
        ),
        row(
            "runtime product policy",
            "artifact_proven_only",
            "Snapshot written when policy/ratio/validation writers run.",
            proof_path=str(ctrl / "runtime_policy_snapshot.json"),
            entrypoint="trading_ai.nte.execution.routing.policy.runtime_coinbase_policy.write_runtime_policy_artifacts",
            tests=["tests/test_runtime_coinbase_policy_unified.py"],
            next_level="Fresh snapshot after NTE_PRODUCTS change",
        ),
        row(
            "universal runtime policy",
            "validation_path_invoked",
            "Embedded in validation diagnostics and runtime snapshot JSON.",
            proof_path=str(ctrl / "runtime_policy_snapshot.json"),
            entrypoint="trading_ai.nte.execution.routing.policy.universal_runtime_policy.build_universal_runtime_policy",
            next_level="None — informational overlay",
        ),
        row(
            "deployable capital truth",
            "artifact_proven_only",
            "Written when validation control artifacts run with client.",
            proof_path=str(ctrl / "deployable_capital_report.json"),
            entrypoint="trading_ai.nte.execution.routing.integration.capital_reports.build_deployable_capital_report",
            tests=["tests/test_universal_capital_routing.py"],
            next_level="Run micro-validation or validation-products with credentials",
        ),
        row(
            "reserve capital truth",
            "wired_not_runtime_proven",
            "Reserve report generated from deployable JSON + ratio bundle; needs prior deployable artifact.",
            proof_path=str(ctrl / "reserve_capital_report.json"),
            entrypoint="trading_ai.ratios.reserve_compute.build_reserve_capital_report",
            tests=["tests/test_universal_ratio_layer.py"],
            next_level="Run python -m trading_ai.ratios write-all after deployable exists",
        ),
        row(
            "ratio policy bundle",
            "advisory_to_runtime_not_enforced",
            "Artifacts + gate views; does not override order guard or sizing alone.",
            proof_path=str(ctrl / "ratio_policy_snapshot.json"),
            entrypoint="trading_ai.ratios.universal_ratio_registry.build_universal_ratio_policy_bundle",
            tests=["tests/test_universal_ratio_layer.py"],
            next_level="Optional: enforce on sizing path (explicit product decision)",
        ),
        row(
            "ratio context on trades",
            "wired_into_live_runtime",
            "Folded into market_snapshot_json when enrich hook adds ratio_context before merge_defaults.",
            entrypoint="trading_ai.nte.databank.trade_intelligence_databank.TradeIntelligenceDatabank.process_closed_trade",
            tests=["tests/test_universal_ratio_layer.py"],
            next_level="Ensure every closed trade supplies trading_gate/strategy_id for rich context",
        ),
        row(
            "databank trade summaries",
            "live_path_invoked",
            "refresh_all_summaries after closed trade when pipeline runs.",
            entrypoint="trading_ai.nte.databank.trade_summary_engine.refresh_all_summaries",
            next_level="Summaries aggregate events; ratio_context visible inside market_snapshot_json per trade",
        ),
        row(
            "daily ratio review",
            "artifact_proven_only",
            "File-based CEO ratio session; no LLM orchestration wired.",
            proof_path=str(rev / "daily_ratio_review.json"),
            entrypoint="trading_ai.ratios.daily_ratio_review.write_daily_ratio_review",
            next_level="External scheduler or readiness hook; optional LLM paste workflow",
        ),
        row(
            "last_48h_system_mastery",
            "artifact_proven_only",
            "Written by ratios CLI or readiness gap closure.",
            proof_path=str(learn / "last_48h_system_mastery.json"),
            entrypoint="trading_ai.ratios.system_mastery.write_last_48h_system_mastery",
            next_level="Run write-everything or readiness",
        ),
        row(
            "recent_work_activation_audit",
            "artifact_proven_only",
            "Explicit writer; lists what is wired vs artifact-proven — not auto on every trade.",
            entrypoint="trading_ai.ratios.recent_work_activation.write_recent_work_activation_audit",
            proof_path=str(ctrl / "recent_work_activation_audit.json"),
            next_level="Refresh after major wiring changes",
        ),
        row(
            "integration_structural_audit",
            "artifact_proven_only",
            "Curated structural map; written to learning + control when CLI or write-everything runs.",
            proof_path=str(ctrl / "integration_structural_audit.json"),
            entrypoint="trading_ai.ratios.integration_structural_audit.write_integration_audit_artifacts",
            next_level="None",
        ),
        row(
            "adaptive operating system",
            "validation_path_invoked",
            "Micro-validation preamble materializes adaptive gate output.",
            entrypoint="trading_ai.control.live_adaptive_integration",
            next_level="Keep adaptive_live_proof.json fresh",
        ),
        row(
            "adaptive routing proof",
            "artifact_proven_only",
            "Proof JSON from adaptive preamble when micro-validation runs.",
            proof_path=str(ctrl / "adaptive_routing_proof.json"),
            next_level="Run micro-validation preamble",
        ),
        row(
            "adaptive live proof",
            "artifact_proven_only",
            "Proof JSON from live adaptive path when exercised.",
            proof_path=str(ctrl / "adaptive_live_proof.json"),
            next_level="Run micro-validation",
        ),
        row(
            "CEO generic review",
            "artifact_proven_only",
            "Generic CEO daily review artifact (not ratio-specific).",
            proof_path=str(runtime_root / "data" / "review" / "ceo_daily_review.json"),
            entrypoint="trading_ai.review.ceo_review_session.build_ceo_daily_review",
            next_level="daily_diagnosis feed",
        ),
        row(
            "CEO ratio review",
            "artifact_proven_only",
            "No dual LLM orchestration — file-based only.",
            proof_path=str(rev / "daily_ratio_review.json"),
            next_level="llm_orchestration_status:not_yet_wired",
        ),
        row(
            "Supabase sync proof",
            "validation_path_invoked",
            "jsonl proof lines when validation writes remote row evidence.",
            proof_path=str(dep / "supabase_proof.jsonl"),
            entrypoint="trading_ai.deployment.supabase_proof.prove_supabase_write",
            next_level="Successful validation trade_id remote row",
        ),
        row(
            "reconciliation proof",
            "validation_path_invoked",
            "jsonl proof when reconciliation runs after trades.",
            proof_path=str(dep / "reconciliation_proof.jsonl"),
            entrypoint="trading_ai.deployment.reconciliation_proof.prove_reconciliation_after_trade",
            next_level="Clean recon after micro-validation",
        ),
        row(
            "route selection report",
            "artifact_proven_only",
            "Emitted with validation control artifact bundle when enabled.",
            proof_path=str(ctrl / "route_selection_report.json"),
            entrypoint="validation_resolve + control artifacts",
            next_level="Validation write",
        ),
        row(
            "portfolio truth snapshot",
            "artifact_proven_only",
            "Coinbase portfolio snapshot for routing when validation writes control artifacts.",
            proof_path=str(ctrl / "portfolio_truth_snapshot.json"),
            next_level="Validation write",
        ),
        row(
            "deployable capital report",
            "artifact_proven_only",
            "Deployable capital slice from validation/micro-validation when API + flags allow.",
            proof_path=str(ctrl / "deployable_capital_report.json"),
            next_level="Validation write",
        ),
        row(
            "final readiness report",
            "live_path_invoked",
            "Written when readiness runs with write_files.",
            proof_path=str(dep / "final_readiness_report.txt"),
            entrypoint="trading_ai.deployment.readiness_decision.compute_final_readiness",
            tests=["tests/test_final_wiring_readiness.py"],
            next_level="None",
        ),
    ]

    # Refresh proof_path existence flags
    for r in matrix:
        pp = r.get("proof_path_if_any")
        if isinstance(pp, str):
            r["artifact_exists_at_audit_time"] = _exists(Path(pp))
        r["distinction_fields"] = _distinction_fields_for_subsystem_row(r)

    return {
        "artifact": "honest_live_status_matrix",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification_legend": {
            "advisory_to_runtime_not_enforced": "Readable context; does not change orders by itself.",
            "artifact_proven_only": "File exists or writer ran — not proof of trading profit.",
            "validation_path_invoked": "Used in validation/micro-validation flows.",
            "intentionally_disabled": "Operator/env keeps subsystem off.",
            "wired_not_runtime_proven": "Code path exists; artifact may be missing.",
        },
        "subsystems": matrix,
        "global_notes": {
            "ratio_framework": "runtime_readable_not_order_enforced unless explicitly wired to sizing later",
            "reserve_framework": "informational_derived_from_deployable_json",
            "git": "git_commit_state_unknown_from_runtime — use git in CI for commit truth",
        },
        "distinction_fields_reference": distinction_fields_reference(),
    }


def build_final_gap_closure_audit(*, runtime_root: Path) -> Dict[str, Any]:
    """Per user section 1 — itemized gap list with honest fields."""
    hm = build_honest_live_status_matrix(runtime_root=runtime_root)
    ctrl = runtime_root / "data" / "control"
    proof_snap = {
        "ratio_policy_snapshot": _exists(ctrl / "ratio_policy_snapshot.json"),
        "deployable_capital_report": _exists(ctrl / "deployable_capital_report.json"),
        "reserve_capital_report": _exists(ctrl / "reserve_capital_report.json"),
    }
    learn = runtime_root / "data" / "learning"
    integration_audit_written = _exists(learn / "integration_structural_audit.json") or _exists(
        ctrl / "integration_structural_audit.json"
    )

    items_raw: List[Dict[str, Any]] = [
        {
            "id": "A_ratio_framework_enforcement",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": proof_snap["ratio_policy_snapshot"],
            "artifact_evidence_at_audit": proof_snap,
            "invoked_by_validation_path": True,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "runtime_readable_not_order_enforced",
            "next_required_step": "Optional future: enforce on position sizing (explicit change)",
        },
        {
            "id": "B_reserve_deployable_enforcement",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": proof_snap["reserve_capital_report"] and proof_snap["deployable_capital_report"],
            "artifact_evidence_at_audit": proof_snap,
            "invoked_by_validation_path": True,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_derived",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "wired_into_validation_artifacts_not_order_guard",
            "next_required_step": "Deployable JSON must exist (run validation with credentials)",
        },
        {
            "id": "C_ratio_context_trade_paths",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": False,
            "artifact_written_in_runtime": False,
            "invoked_by_validation_path": False,
            "invoked_by_live_path": True,
            "enforced_vs_informational": "advisory_to_runtime",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "wired_into_databank_closed_trade_pipeline",
            "next_required_step": "Ensure trading_gate populated on events for richer context",
        },
        {
            "id": "D_daily_ratio_review",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(runtime_root / "data" / "review" / "daily_ratio_review.json"),
            "invoked_by_validation_path": False,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "file_based_llm_orchestration_not_yet_wired",
            "next_required_step": "Scheduler or ops: run python -m trading_ai.ratios daily-review",
        },
        {
            "id": "E_last_48h_mastery",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(runtime_root / "data" / "learning" / "last_48h_system_mastery.json"),
            "invoked_by_validation_path": False,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "not_yet_invoked_until_cli_or_readiness",
            "next_required_step": "readiness write_files or ratios mastery command",
        },
        {
            "id": "F_recent_work_activation_audit",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(ctrl / "recent_work_activation_audit.json"),
            "invoked_by_validation_path": False,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "artifact_proven_when_writer_runs",
            "next_required_step": "gap_closure or ratios write-everything",
        },
        {
            "id": "G_integration_structural_audit",
            "exists_in_repo": True,
            "has_tests": False,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": integration_audit_written,
            "invoked_by_validation_path": False,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "curated_static_map",
            "next_required_step": "Maintain list when subsystems change",
        },
        {
            "id": "H_Gate_A_ratio_bundle_consumption",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": False,
            "artifact_written_in_runtime": False,
            "invoked_by_validation_path": True,
            "invoked_by_live_path": True,
            "enforced_vs_informational": "advisory_to_runtime",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "read_first_gate_hooks_and_gate_a_live_truth",
            "next_required_step": "None for read path",
        },
        {
            "id": "I_Gate_B_ratio_bundle_consumption",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": False,
            "artifact_written_in_runtime": False,
            "invoked_by_validation_path": False,
            "invoked_by_live_path": True,
            "enforced_vs_informational": "advisory_to_runtime",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "gate_b_live_status_report_includes_ratio_advisory",
            "next_required_step": "Enable Gate B only with validation artifact if going live",
        },
        {
            "id": "J_adaptive_multiplier_plumbing",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": False,
            "artifact_written_in_runtime": False,
            "invoked_by_validation_path": True,
            "invoked_by_live_path": True,
            "enforced_vs_informational": "mixed_adaptive_os_elsewhere",
            "safe_to_wire_now": False,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "placeholder_in_ratio_bundle",
            "next_required_step": "Export multipliers from adaptive OS into ratio snapshot explicitly",
        },
        {
            "id": "K_artifact_refresh_cadence",
            "exists_in_repo": True,
            "has_tests": False,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": proof_snap["deployable_capital_report"],
            "invoked_by_validation_path": True,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "validation_preflight_writes_capital_ratio_refresh_optional",
            "next_required_step": "readiness runs gap_closure for matrix refresh",
        },
        {
            "id": "L_CEO_ratio_LLM_orchestration",
            "exists_in_repo": False,
            "has_tests": False,
            "has_artifact_writer": False,
            "artifact_written_in_runtime": False,
            "invoked_by_validation_path": False,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "n/a",
            "safe_to_wire_now": False,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "not_in_repo_dual_llm_ceo_ratio",
            "next_required_step": "External workflow or new module — do not fake",
        },
        {
            "id": "M_universal_runtime_policy_snapshot_refresh",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(ctrl / "runtime_policy_snapshot.json"),
            "artifact_evidence_at_audit": proof_snap,
            "invoked_by_validation_path": True,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "wired_into_validation_and_policy_writers",
            "next_required_step": "Run scripts/runtime_policy_snapshot.py or validation with policy writes",
        },
        {
            "id": "N_deployable_capital_report_refresh",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": proof_snap["deployable_capital_report"],
            "artifact_evidence_at_audit": proof_snap,
            "invoked_by_validation_path": True,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "validation_control_artifact_path",
            "next_required_step": "Credentials + EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS or micro-validation",
        },
        {
            "id": "O_route_selection_report_refresh",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(ctrl / "route_selection_report.json"),
            "invoked_by_validation_path": True,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "same_source_as_deployable_bundle",
            "next_required_step": "Same as deployable capital report refresh",
        },
        {
            "id": "P_portfolio_truth_snapshot_refresh",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(ctrl / "portfolio_truth_snapshot.json"),
            "invoked_by_validation_path": True,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "validation_preflight_when_client_available",
            "next_required_step": "Run validation-products or micro-validation with API access",
        },
        {
            "id": "Q_databank_summary_ratio_context_awareness",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": False,
            "artifact_written_in_runtime": False,
            "invoked_by_validation_path": False,
            "invoked_by_live_path": True,
            "enforced_vs_informational": "advisory_to_runtime",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "ratio_context_in_market_snapshot_json_when_enriched",
            "next_required_step": "Summaries aggregate rows; inspect weekly/daily JSON for folded ratio_context",
        },
        {
            "id": "R_final_readiness_and_final_report_honesty",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(runtime_root / "data" / "deployment" / "final_readiness_report.txt"),
            "invoked_by_validation_path": False,
            "invoked_by_live_path": True,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": False,
            "current_truth_status": "readiness_emits_live_truth_plain_english_and_matrix_refs",
            "next_required_step": "compute_final_readiness(write_files=True) after checklist",
        },
        {
            "id": "S_last_48h_modules_not_on_validation_runtime",
            "exists_in_repo": True,
            "has_tests": True,
            "has_artifact_writer": True,
            "artifact_written_in_runtime": _exists(learn / "last_48h_system_mastery.json"),
            "invoked_by_validation_path": False,
            "invoked_by_live_path": False,
            "enforced_vs_informational": "informational_only",
            "safe_to_wire_now": True,
            "should_remain_honestly_not_live": True,
            "current_truth_status": "proven_in_tests_only_until_cli_or_readiness_invokes_writer",
            "next_required_step": "ratios mastery / write-everything / readiness gap_closure",
        },
    ]

    items: List[Dict[str, Any]] = []
    for raw in items_raw:
        merged = dict(raw)
        merged["distinction_fields"] = _distinction_fields_for_gap_item(raw)
        items.append(merged)

    return {
        "artifact": "final_gap_closure_audit",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime_root),
        "honest_live_status_matrix_ref": str(ctrl / "honest_live_status_matrix.json"),
        "distinction_fields_reference": distinction_fields_reference(),
        "items": items,
        "embedded_matrix_summary": {"subsystem_count": len(hm.get("subsystems") or [])},
    }


def write_honest_gap_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, str]:
    from trading_ai.runtime_paths import ezras_runtime_root

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)

    hm = build_honest_live_status_matrix(runtime_root=root)
    gap = build_final_gap_closure_audit(runtime_root=root)

    out: Dict[str, str] = {}
    for name, payload in (
        ("honest_live_status_matrix", hm),
        ("final_gap_closure_audit", gap),
    ):
        js = json.dumps(payload, indent=2, default=str)
        (ctrl / f"{name}.json").write_text(js, encoding="utf-8")
        (ctrl / f"{name}.txt").write_text(js[:28000] + "\n", encoding="utf-8")
        out[f"{name}_json"] = str(ctrl / f"{name}.json")

    return out
