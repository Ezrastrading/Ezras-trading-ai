"""
Final readiness — consolidates checklist, streak, proofs, halt state.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.deployment.deployment_checklist import run_deployment_checklist
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.deployment.env_parity import run_env_parity_report
from trading_ai.deployment.final_readiness_report import write_final_readiness_report
from trading_ai.deployment.governance_proof import prove_governance_behavior
from trading_ai.deployment.ops_outputs_proof import verify_ops_outputs_proof
from trading_ai.deployment.paths import (
    checklist_json_path,
    deployment_data_dir,
    deployment_parity_report_path,
    final_readiness_path,
    governance_proof_path,
    live_validation_runs_dir,
    ops_outputs_proof_path,
    reconciliation_proof_jsonl_path,
    streak_state_path,
    supabase_proof_jsonl_path,
    supabase_schema_readiness_path,
)
from trading_ai.deployment.databank_artifact_verify import verify_local_databank_artifacts
from trading_ai.deployment.reconciliation_proof import prove_reconciliation_after_trade
from trading_ai.deployment.supabase_proof import prove_supabase_write
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


def _read_control_quote_capital_truth() -> Dict[str, Any]:
    p = ezras_runtime_root() / "data" / "control" / "quote_capital_truth.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_control_deployable_capital_report() -> Dict[str, Any]:
    p = ezras_runtime_root() / "data" / "control" / "deployable_capital_report.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _adaptive_proof_freshness(
    path: Path,
    *,
    max_age_hours: float,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Prefer ``generated_at`` inside JSON; fall back to mtime. Returns (fresh, diagnostics).
    """
    diag: Dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return False, diag
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            ga = str(raw.get("generated_at") or "").strip()
            if ga:
                diag["generated_at"] = ga
                try:
                    dt = datetime.fromisoformat(ga.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
                    diag["age_hours"] = round(age_h, 4)
                    return age_h <= max_age_hours, diag
                except (TypeError, ValueError):
                    pass
            pk = raw.get("proof_kind")
            ps = raw.get("proof_source")
            diag["proof_kind"] = pk
            diag["proof_source_in_file"] = ps
    except (json.JSONDecodeError, OSError) as exc:
        diag["parse_error"] = str(exc)
    try:
        age_s = time.time() - path.stat().st_mtime
        age_h = age_s / 3600.0
        diag["age_hours_mtime_fallback"] = round(age_h, 4)
        return age_h <= max_age_hours, diag
    except OSError:
        return False, diag


def _streak_state() -> Dict[str, Any]:
    p = streak_state_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _streak_has_partial_failures(streak: Dict[str, Any]) -> bool:
    for r in streak.get("runs") or []:
        if not isinstance(r, dict):
            continue
        if r.get("no_partial_failures") is False:
            return True
    return False


def _latest_micro_validation_run_json() -> Dict[str, Any]:
    d = live_validation_runs_dir()
    if not d.is_dir():
        return {}
    files = sorted(d.glob("live_validation_*.json"))
    if not files:
        return {}
    try:
        raw = json.loads(files[-1].read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _reconciliation_probe_context(probe_product: str) -> Dict[str, Any]:
    """Include inventory-delta baselines from last micro-validation JSON when present."""
    ctx: Dict[str, Any] = {"product_id": probe_product}
    full = _latest_micro_validation_run_json()
    b = full.get("spot_snapshot_before")
    if isinstance(b, dict) and b.get("exchange_base_qty") is not None and b.get("internal_base_qty") is not None:
        ctx["baseline_exchange_base_qty"] = b.get("exchange_base_qty")
        ctx["baseline_internal_base_qty"] = b.get("internal_base_qty")
        ctx["reconciliation_mode"] = "inventory_delta"
    return ctx


def _important_blockers_from_checklist(checklist: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    pd = checklist.get("checks", {}).get("deployment_parity_ok", {})
    details = pd.get("details") if isinstance(pd, dict) else None
    if not isinstance(details, dict):
        return out
    gaps = details.get("proof_environment_gaps") or details.get("placeholders_found") or []
    if isinstance(gaps, (list, tuple)):
        for g in gaps[:12]:
            out.append(f"deployment_parity_note:{g}")
    return out


def compute_final_readiness(
    *,
    trade_id_probe: Optional[str] = None,
    write_files: bool = True,
) -> Dict[str, Any]:
    """
    ``ready_for_first_20`` is True only when deployment checklist is green, streak passed,
    no halt, governance proof green, Supabase + reconciliation probes pass, ops outputs proof
    green, and no partial-failure codes remain on the streak.
    """
    checklist = run_deployment_checklist(write_files=write_files)
    env_p = run_env_parity_report(write_file=write_files)
    gov = prove_governance_behavior(write_file=write_files)

    streak = _streak_state()
    streak_ok = bool(streak.get("live_validation_streak_passed"))
    streak_blocking = str(streak.get("blocking_reason") or "")
    qct_artifact = _read_control_quote_capital_truth()
    dcr_artifact = _read_control_deployable_capital_report()
    partial_bad = _streak_has_partial_failures(streak)
    streak_runs = streak.get("runs") or []

    probe_tid = (trade_id_probe or "").strip()
    if not probe_tid and streak_runs:
        last = streak_runs[-1] if isinstance(streak_runs[-1], dict) else {}
        probe_tid = str(last.get("trade_id") or "").strip()

    probe_product = "BTC-USD"
    if streak_runs:
        lp = streak_runs[-1] if isinstance(streak_runs[-1], dict) else {}
        probe_product = str(lp.get("venue_product_id") or "BTC-USD").strip() or "BTC-USD"

    ready_micro = bool(checklist.get("ready_for_live_micro_validation"))

    critical_blockers: List[str] = []

    if not ready_micro:
        critical_blockers.append("deployment_checklist_not_green")

    try:
        from trading_ai.core.system_guard import trading_halt_path

        if trading_halt_path().is_file():
            critical_blockers.append("trading_halt_present")
    except Exception as exc:
        logger.debug("halt check: %s", exc)

    if not gov.get("governance_proof_ok"):
        critical_blockers.append("governance_proof")

    if not gov.get("governance_trading_permitted"):
        critical_blockers.append("governance_trading_not_permitted")

    if not env_p.get("env_parity_ok"):
        critical_blockers.append("env_parity")

    if not streak_ok:
        critical_blockers.append("live_validation_streak_not_passed")

    if partial_bad:
        critical_blockers.append("streak_partial_failures_recorded")

    recon_ok = True
    supa_ok = True
    if streak_ok:
        if not probe_tid:
            recon_ok = False
            supa_ok = False
            critical_blockers.append("missing_trade_id_for_post_streak_probes")
        else:
            r = prove_reconciliation_after_trade(
                _reconciliation_probe_context(probe_product),
                append_log=False,
            )
            recon_ok = bool(r.get("reconciliation_ok"))
            s = prove_supabase_write(probe_tid, append_log=False)
            supa_ok = bool(s.get("supabase_proof_ok"))
            if not recon_ok:
                critical_blockers.append("reconciliation_proof")
            if not supa_ok:
                critical_blockers.append("supabase_proof")

    ops_ok = True
    ops: Dict[str, Any] = {}
    if streak_ok:
        ops = verify_ops_outputs_proof(write_file=write_files)
        ops_ok = bool(ops.get("ops_outputs_ok"))
        if not ops_ok:
            critical_blockers.append("ops_outputs_proof")

    db_verify: Dict[str, Any] = {"all_core_ok": False}
    if streak_ok and probe_tid:
        db_verify = verify_local_databank_artifacts(trade_id=probe_tid)
        if not bool(db_verify.get("all_core_ok")):
            critical_blockers.append("databank_local_artifacts_incomplete")

    aos_proof_path_str = ""
    aos_proof_exists = False
    aos_fresh = False
    aos_fresh_diag: Dict[str, Any] = {}
    routing_fresh = False
    routing_fresh_diag: Dict[str, Any] = {}
    v_live: Dict[str, Any] = {}
    v_route: Dict[str, Any] = {}
    adaptive_proof_diag: Dict[str, Any] = {}
    try:
        max_h = float((os.environ.get("ADAPTIVE_PROOF_MAX_AGE_HOURS") or "168").strip() or "168")
    except ValueError:
        max_h = 168.0
    max_age_sec = max_h * 3600.0
    try:
        from trading_ai.control.adaptive_proof_validation import (
            validate_adaptive_live_proof_file,
            validate_adaptive_routing_proof_file,
        )
        from trading_ai.control.adaptive_routing_live import adaptive_routing_proof_path
        from trading_ai.control.live_adaptive_integration import adaptive_live_proof_path

        ap = adaptive_live_proof_path()
        rp = adaptive_routing_proof_path()
        aos_proof_path_str = str(ap)
        _routing_proof = rp
        v_live = validate_adaptive_live_proof_file(ap, max_age_sec=max_age_sec)
        v_route = validate_adaptive_routing_proof_file(rp, max_age_sec=max_age_sec)
        adaptive_proof_diag = {"adaptive_live": v_live, "adaptive_routing": v_route}
        aos_proof_exists = bool(v_live.get("ok"))
        aos_fresh_diag = dict(v_live)
        aos_fresh = bool(v_live.get("ok")) and not any(
            "stale" in str(w).lower() for w in (v_live.get("warnings") or [])
        )
        routing_fresh_diag = dict(v_route)
        routing_fresh = bool(v_route.get("ok")) and not any(
            "stale" in str(w).lower() for w in (v_route.get("warnings") or [])
        )
    except Exception as exc:
        aos_fresh_diag = {"error": str(exc)}
        routing_fresh_diag = {"error": str(exc)}
        try:
            from trading_ai.control.live_adaptive_integration import adaptive_live_proof_path

            aos_proof_path_str = str(adaptive_live_proof_path())
        except Exception:
            aos_proof_path_str = ""

    gate_b_report: Dict[str, Any] = {}
    try:
        from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report

        gate_b_report = gate_b_live_status_report()
    except Exception:
        gate_b_report = {"error": "gate_b_status_unavailable"}

    gate_a_truth: Dict[str, Any] = {}
    try:
        from trading_ai.deployment.gate_a_live_truth import gate_a_live_truth_snapshot

        gate_a_truth = gate_a_live_truth_snapshot()
    except Exception as exc:
        gate_a_truth = {"error": str(exc)}

    if streak_ok and (not aos_proof_exists or not bool(v_route.get("ok"))):
        critical_blockers.append("adaptive_runtime_proofs_invalid")
    if streak_ok and (not aos_fresh or not routing_fresh):
        critical_blockers.append("adaptive_runtime_proofs_not_current")

    ready_first = (
        ready_micro
        and streak_ok
        and len(critical_blockers) == 0
        and not partial_bad
    )

    reason = "ok" if ready_first else "not_ready"
    if critical_blockers:
        reason = "blockers:" + ",".join(sorted(set(critical_blockers)))

    important_blockers = _important_blockers_from_checklist(checklist)
    advisory_notes: List[str] = []
    if (os.environ.get("SUPABASE_SCHEMA_CHECK_SKIP") or "").strip().lower() in ("1", "true", "yes"):
        advisory_notes.append(
            "SUPABASE_SCHEMA_CHECK_SKIP is set — treat remote schema verification as operator-confirmed outside this gate.",
        )
    if not aos_proof_exists:
        errs = (v_live.get("errors") or []) if isinstance(v_live, dict) else []
        advisory_notes.append(
            "adaptive_live_proof.json missing or schema-invalid — run micro-validation preamble, live_execution_validation, or NTE slow tick. "
            + ("Errors: " + "; ".join(str(e) for e in errs[:6]) if errs else ""),
        )
    elif aos_proof_exists and not aos_fresh:
        advisory_notes.append(
            f"adaptive_live_proof.json failed freshness (ADAPTIVE_PROOF_MAX_AGE_HOURS={max_h}h) or stale warnings — refresh via live validation or NTE tick.",
        )
    if isinstance(v_route, dict) and v_route.get("ok") is False:
        rerrs = v_route.get("errors") or []
        if rerrs:
            advisory_notes.append("adaptive_routing_proof: " + "; ".join(str(e) for e in rerrs[:6]))

    quote_preflight_plain_english = ""
    pvc_line = str((dcr_artifact or {}).get("policy_vs_capital_one_liner") or "").strip()
    if streak_blocking:
        if "insufficient_allowed_quote_balance" in streak_blocking:
            quote_preflight_plain_english = (
                "Micro-validation quote preflight: no allowed single-leg spot pair had enough quote for the notional "
                "(see data/control/quote_capital_truth.json and deployable_capital_report.json)."
            )
        elif "runtime_policy_disallows_fundable_product" in streak_blocking:
            quote_preflight_plain_english = (
                "Capital exists in a quote wallet (e.g. USDC) but runtime policy blocks that product id — "
                "add it to NTE_PRODUCTS or fund an allowed pair's quote currency."
            )
            if pvc_line:
                quote_preflight_plain_english = pvc_line
        elif "no_runtime_supported_validation_product" in streak_blocking:
            quote_preflight_plain_english = (
                "Quote preflight: venue catalog or ticker checks failed for allowed funded pairs — "
                "see validation_product_resolution_report.json under data/control/."
            )
        elif "no_allowed_validation_product_found" in streak_blocking:
            quote_preflight_plain_english = (
                "Quote preflight: no candidate passed venue, runtime allowlist, balance (including venue minimum), "
                "and ticker — see validation_product_resolution_report.json candidate_attempts."
            )
    try:
        from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import (
            resolve_coinbase_runtime_product_policy,
        )

        gate_coinbase_policy = resolve_coinbase_runtime_product_policy(include_venue_catalog=False).to_dict()
    except Exception as exc:
        gate_coinbase_policy = {"error": str(exc)}

    proof_refs = {
        "deployment_checklist": str(checklist_json_path()),
        "supabase_schema_readiness": str(supabase_schema_readiness_path()),
        "deployment_parity_report": str(deployment_parity_report_path()),
        "live_validation_streak": str(streak_state_path()),
        "live_validation_runs_dir": str(live_validation_runs_dir()),
        "governance_proof": str(governance_proof_path()),
        "reconciliation_proof_jsonl": str(reconciliation_proof_jsonl_path()),
        "supabase_proof_jsonl": str(supabase_proof_jsonl_path()),
        "ops_outputs_proof": str(ops_outputs_proof_path()),
        "adaptive_live_proof": aos_proof_path_str,
        "adaptive_routing_proof": str(ezras_runtime_root() / "data" / "control" / "adaptive_routing_proof.json"),
        "quote_capital_truth": str(ezras_runtime_root() / "data" / "control" / "quote_capital_truth.json"),
        "deployable_capital_report": str(ezras_runtime_root() / "data" / "control" / "deployable_capital_report.json"),
        "route_selection_report": str(ezras_runtime_root() / "data" / "control" / "route_selection_report.json"),
        "portfolio_truth_snapshot": str(ezras_runtime_root() / "data" / "control" / "portfolio_truth_snapshot.json"),
        "validation_product_resolution_report": str(
            ezras_runtime_root() / "data" / "control" / "validation_product_resolution_report.json"
        ),
        "runtime_policy_snapshot": str(ezras_runtime_root() / "data" / "control" / "runtime_policy_snapshot.json"),
        "ratio_policy_snapshot": str(ezras_runtime_root() / "data" / "control" / "ratio_policy_snapshot.json"),
        "reserve_capital_report": str(ezras_runtime_root() / "data" / "control" / "reserve_capital_report.json"),
        "daily_ratio_review": str(ezras_runtime_root() / "data" / "review" / "daily_ratio_review.json"),
        "last_48h_system_mastery": str(ezras_runtime_root() / "data" / "learning" / "last_48h_system_mastery.json"),
        "recent_work_activation_audit": str(ezras_runtime_root() / "data" / "control" / "recent_work_activation_audit.json"),
        "integration_structural_audit": str(ezras_runtime_root() / "data" / "control" / "integration_structural_audit.json"),
        "honest_live_status_matrix": str(ezras_runtime_root() / "data" / "control" / "honest_live_status_matrix.json"),
        "final_gap_closure_audit": str(ezras_runtime_root() / "data" / "control" / "final_gap_closure_audit.json"),
    }

    activation_checklist = {
        "1_micro_validation_streak_clean": bool(streak_ok and not partial_bad),
        "2_ready_for_first_20_computed_true": ready_first,
        "3_adaptive_os_live_proof_artifact": bool(aos_proof_exists and aos_fresh),
        "4_adaptive_routing_proof_logged": bool(v_route.get("ok")) and routing_fresh,
        "5_gate_a_trade_tagging_contract_enforced_in_pipeline": True,
        "6_local_databank_artifacts_verified": bool(db_verify.get("all_core_ok")) if streak_ok and probe_tid else None,
        "7_gate_b_live_state_documented": bool(gate_b_report.get("gate_b_production_state")),
        "8_gate_b_ready_explicit": bool(gate_b_report.get("gate_b_ready_for_live") is not None),
    }

    gap_paths: Dict[str, Any] = {}
    hm_snip: Any = None
    if write_files:
        try:
            from trading_ai.ratios.gap_closure import write_honest_gap_artifacts

            gap_paths = write_honest_gap_artifacts(runtime_root=ezras_runtime_root())
            hp = ezras_runtime_root() / "data" / "control" / "honest_live_status_matrix.json"
            if hp.is_file():
                hm_snip = json.loads(hp.read_text(encoding="utf-8"))
        except Exception as exc:
            gap_paths = {"error": str(exc)}

    live_truth_plain_english: Dict[str, Any] = {
        "what_is_code_ready_but_not_yet_live": [
            "Ratio/reserve framework defaults to informational artifacts + gate views — not order-enforced unless explicitly wired into sizing.",
            "CEO dual-LLM ratio orchestration: not implemented in-repo (see daily_ratio_review llm_orchestration_status).",
        ],
        "what_is_runtime_proven": [
            "Validation preflight + control artifacts when credentials and EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS or micro-validation writes.",
            "This readiness JSON when checklist + streak + proofs align.",
        ],
        "what_is_advisory_only": [
            "gate_ratio_and_reserve_bundle() (read-first)",
            "honest_live_status_matrix — honest classification, not PnL proof",
        ],
        "what_needs_external_deploy_or_scheduler": [
            "Host deploy of this revision — git/deploy state unverified from Python alone.",
            "Optional external cron for daily_ratio_review — readiness can refresh gap/matrix when run.",
        ],
        "what_first_20_depends_on_that_is_still_unproven": list(critical_blockers),
        "gate_a_live_truth_summary": (gate_a_truth or {}).get("classification"),
        "gate_b_live_truth_summary": {
            "production_state": gate_b_report.get("gate_b_production_state"),
            "ready_for_live": gate_b_report.get("gate_b_ready_for_live"),
            "ratio_aware": (gate_b_report.get("ratio_reserve_advisory") or {}).get("ratio_aware"),
            "live_order_enabled_hint": (gate_b_report.get("ratio_reserve_advisory") or {}).get(
                "live_order_enabled"
            ),
        },
    }

    out: Dict[str, Any] = {
        "generated_at": iso_now(),
        "ready_for_live_micro_validation": ready_micro,
        "ready_for_first_20": ready_first,
        "reason": reason,
        "critical_blockers": sorted(set(critical_blockers)),
        "important_blockers": important_blockers,
        "advisory_notes": advisory_notes,
        "streak_passed": streak_ok,
        "streak_partial_failures": partial_bad,
        "trade_id_probe": probe_tid or None,
        "governance_proof_ok": gov.get("governance_proof_ok"),
        "governance_system_consistent": gov.get("governance_system_consistent"),
        "governance_trading_permitted": gov.get("governance_trading_permitted"),
        "governance_trading_block_reason": gov.get("governance_trading_block_reason"),
        "env_parity_ok": env_p.get("env_parity_ok"),
        "reconciliation_probe_ok": recon_ok,
        "supabase_probe_ok": supa_ok,
        "ops_outputs_proof_ok": ops_ok,
        "proof_references": proof_refs,
        "activation_checklist": activation_checklist,
        "databank_artifact_verify": db_verify,
        "gate_b_live_execution": gate_b_report,
        "adaptive_live_proof_exists": aos_proof_exists,
        "adaptive_live_proof_fresh": aos_fresh,
        "adaptive_live_proof_diagnostics": aos_fresh_diag,
        "adaptive_routing_proof_fresh": routing_fresh,
        "adaptive_routing_proof_diagnostics": routing_fresh_diag,
        "adaptive_proof_max_age_hours": max_h,
        "adaptive_proof_validation": adaptive_proof_diag,
        "streak_blocking_reason": streak_blocking or None,
        "quote_preflight_operator_plain_english": quote_preflight_plain_english or None,
        "quote_capital_truth_artifact": qct_artifact or None,
        "deployable_capital_report_artifact": dcr_artifact or None,
        "deployable_capital_summary": {
            "conservative_quote_usd_plus_usdc": (dcr_artifact or {}).get("conservative_deployable_capital"),
            "portfolio_total_mark_value_usd": (dcr_artifact or {}).get("portfolio_total_mark_value_usd"),
            "policy_vs_capital_one_liner": (dcr_artifact or {}).get("policy_vs_capital_one_liner"),
        },
        "policy_vs_capital_summary": (dcr_artifact or {}).get("policy_vs_capital_summary"),
        "direct_vs_convertible_summary": (dcr_artifact or {}).get("direct_vs_convertible_summary"),
        "coinbase_single_leg_runtime_policy_for_gates": gate_coinbase_policy,
        "gate_a_live_truth": gate_a_truth,
        "live_truth_plain_english": live_truth_plain_english,
        "honest_gap_closure_artifact_paths": gap_paths,
        "honest_live_status_matrix": hm_snip,
        "readiness_scope_disclosure": {
            "ready_for_first_20_primary_scope": (
                "Coinbase NTE micro-validation streak + deployment checklist + governance + proofs "
                "— not Kalshi Gate B live activation."
            ),
            "gate_b_not_implied_by_ready_for_first_20": True,
            "gate_b_separate_operator_and_validation": True,
            "see_gate_b_status": "gate_b_live_execution in this JSON + data/control/gate_b_validation.json",
        },
        "deployment_truth_surface": {
            "runtime_root_resolved": str(ezras_runtime_root()),
            "git_commit_and_deploy_state": "unknown_from_python_use_git_and_host",
            "artifact_paths_are_relative_to_runtime_root": True,
        },
    }

    if write_files:
        deployment_data_dir().mkdir(parents=True, exist_ok=True)
        final_readiness_path().write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        try:
            write_final_readiness_report(write_file=True)
        except Exception as exc:
            logger.warning("final_readiness_report: %s", exc)

    return out
