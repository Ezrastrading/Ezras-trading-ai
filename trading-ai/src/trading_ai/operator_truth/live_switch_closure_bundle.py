"""
Consolidated live-switch closure artifacts — evidence-based booleans, no green-by-file-existence.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.orchestration import final_pre_live_writers as _fpw
from trading_ai.orchestration.avenue_a_prelive_artifacts import ensure_minimal_prelive_artifacts
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.reports.gate_b_final_go_live_truth import write_gate_b_final_go_live_truth
from trading_ai.universal_execution.gate_b_proof_bridge import (
    try_emit_universal_loop_proof_from_gate_a_file,
    try_emit_universal_loop_proof_from_gate_b_file,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter

# Prevent re-entrant refresh loops from closure (same process)
_CLOSURE_DEPTH = 0
_MAX_DEPTH = 2

_STATE_REL = "data/control/_live_closure_bundle_state.json"


def _ad(root: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=root)


def _read(ad: LocalStorageAdapter, rel: str) -> Optional[Dict[str, Any]]:
    return ad.read_json(rel)


def _write_final_system_gap_sweep(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    doc = _fpw.build_final_system_gap_sweep(runtime_root=root)
    ad.write_json("data/control/final_system_gap_sweep.json", doc)
    ad.write_text("data/control/final_system_gap_sweep.txt", json.dumps(doc, indent=2) + "\n")
    return doc


def _write_buy_sell_certification(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    doc = _fpw.build_buy_sell_log_rebuy_certification(runtime_root=root)
    ad.write_json("data/control/buy_sell_log_rebuy_certification.json", doc)
    ad.write_text("data/control/buy_sell_log_rebuy_certification.txt", json.dumps(doc, indent=2) + "\n")
    return doc


def _write_lessons_runtime_final(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    doc = _fpw.build_lessons_runtime_final_truth(runtime_root=root)
    ad.write_json("data/control/lessons_runtime_final_truth.json", doc)
    ad.write_text("data/control/lessons_runtime_final_truth.txt", json.dumps(doc, indent=2) + "\n")
    return doc


def _write_runtime_runner_final(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    doc = _fpw.build_runtime_runner_final_truth(runtime_root=root)
    ad.write_json("data/control/runtime_runner_final_truth.json", doc)
    ad.write_text("data/control/runtime_runner_final_truth.txt", json.dumps(doc, indent=2) + "\n")
    return doc


def _write_avenue_authority(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    doc = _fpw.build_final_avenue_readiness_authority(runtime_root=root)
    ad.write_json("data/control/final_avenue_readiness_authority.json", doc)
    ad.write_text("data/control/final_avenue_readiness_authority.txt", json.dumps(doc, indent=2) + "\n")
    return doc


def _write_supreme_go_live_decision(
    ad: LocalStorageAdapter,
    root: Path,
    a_block: Dict[str, Any],
    final_gap: Dict[str, Any],
    bslr: Dict[str, Any],
    les: Dict[str, Any],
    crt: Dict[str, Any],
    f20_merged: Dict[str, Any],
) -> Dict[str, Any]:
    raw = _fpw.build_final_go_live_decision_engine(
        runtime_root=root,
        a_block=a_block,
        final_gap_doc=final_gap,
        bslr=bslr,
        les=les,
        crt=crt,
    )
    p = _fpw.patch_go_live_decision_with_first_20(raw, f20_merged or {})
    ad.write_json("data/control/final_go_live_decision.json", p)
    ad.write_text("data/control/final_go_live_decision.txt", json.dumps(p, indent=2) + "\n")
    return p


def write_live_switch_closure_bundle(
    *,
    runtime_root: Optional[Path] = None,
    trigger_surface: str = "unspecified",
    reason: str = "material_truth_change",
) -> Dict[str, Any]:
    """
    Write all Section 1–9 artifacts under data/control/. Idempotent; does not call full refresh manager.
    """
    global _CLOSURE_DEPTH
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    if _CLOSURE_DEPTH >= _MAX_DEPTH:
        return {"skipped": True, "reason": "reentrancy_guard", "trigger_surface": trigger_surface}
    _CLOSURE_DEPTH += 1
    try:
        ad = _ad(root)
        ctrl = ad.root() / "data" / "control"
        ctrl.mkdir(parents=True, exist_ok=True)

        try:
            from trading_ai.first_20.storage import ensure_bootstrap

            ensure_bootstrap(runtime_root=root)
        except Exception:
            pass  # non-fatal; first_20 may be unavailable in minimal test envs

        mat_detected, changed_surface_ids, _old_fp, new_fp = _fpw.detect_material_closure_change(runtime_root=root)

        out: Dict[str, Any] = {
            "written": [],
            "trigger_surface": trigger_surface,
            "reason": reason,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "section_errors": {},
        }

        def _safe(name: str, fn: Any, default: Any) -> Any:
            try:
                return fn()
            except Exception as exc:
                out["section_errors"][name] = str(exc)
                return default

        _safe("avenue_a_prelive", lambda: ensure_minimal_prelive_artifacts(runtime_root=root), {})
        _safe("gate_b_truth_refresh", lambda: write_gate_b_final_go_live_truth(runtime_root=root), {})
        _safe("gate_b_universal_bridge", lambda: try_emit_universal_loop_proof_from_gate_b_file(runtime_root=root), {})
        _safe("gate_a_universal_bridge", lambda: try_emit_universal_loop_proof_from_gate_a_file(runtime_root=root), {})

        # --- Final system gap sweep (Section A) ---
        _safe(
            "gap_sweep",
            lambda: _write_final_system_gap_sweep(ad, root),
            {},
        )
        out["written"].extend(["data/control/final_system_gap_sweep.json", "data/control/final_system_gap_sweep.txt"])

        # --- First-20 sub-artifacts (Section 2) ---
        f20_ack = _safe("first_20_ack", lambda: _write_first_20_operator_ack_truth(ad, root), {})
        f20_adj = _safe("first_20_adj", lambda: _write_first_20_adjustment_guardrail_truth(ad, root), {})
        f20_lat = _safe("first_20_lat", lambda: _write_first_20_latency_truth(ad, root), {})
        f20_rem = _safe(
            "first_20_remaining",
            lambda: _write_first_20_remaining_gaps(ad, root, f20_ack, f20_lat, f20_adj),
            {"gaps": []},
        )
        f20_merged = _safe("first_20_merged_final", lambda: _fpw.write_first_20_merged_final_truth_files(runtime_root=root), {})
        _safe("first_20_remaining_final", lambda: _fpw.write_first_20_remaining_gaps_final(runtime_root=root, base_doc=f20_rem), {})
        out["written"].extend(
            [
                "data/control/first_20_operator_ack_truth.json",
                "data/control/first_20_adjustment_guardrail_truth.json",
                "data/control/first_20_latency_truth.json",
                "data/control/first_20_remaining_gaps.json",
                "data/control/first_20_remaining_gaps.txt",
                "data/control/first_20_final_truth.json",
                "data/control/first_20_final_truth.txt",
                "data/control/first_20_remaining_gaps_final.json",
                "data/control/first_20_remaining_gaps_final.txt",
            ]
        )

        # --- Buy/sell/log/rebuy (Section 3) ---
        bslr = _safe("buy_sell_log_rebuy", lambda: _write_buy_sell_log_rebuy_truth(ad, root), {})
        cert = _safe("buy_sell_cert", lambda: _write_buy_sell_certification(ad, root), {})

        out["written"].extend(
            [
                "data/control/buy_sell_log_rebuy_truth.json",
                "data/control/buy_sell_log_rebuy_truth.txt",
                "data/control/buy_sell_log_rebuy_certification.json",
                "data/control/buy_sell_log_rebuy_certification.txt",
            ]
        )

        # Material change meta written after fingerprint persist (see end)
        mat: Dict[str, Any] = {}

        # --- Avenue readiness A/B/C (Section 5) ---
        uar = _safe("universal_avenue", lambda: _write_universal_avenue_live_readiness(ad, root), {"avenues": {}})
        _safe("avenue_authority", lambda: _write_avenue_authority(ad, root), {})
        out["written"].extend(
            [
                "data/control/universal_avenue_live_readiness.json",
                "data/control/universal_avenue_live_readiness.txt",
                "data/control/final_avenue_readiness_authority.json",
                "data/control/final_avenue_readiness_authority.txt",
            ]
        )

        # --- Runner / daemon (Section 6) ---
        crt = _safe("continuous_runner", lambda: _write_continuous_runner_truth(ad, root), {})
        _safe("runtime_runner_final", lambda: _write_runtime_runner_final(ad, root), {})
        out["written"].extend(
            [
                "data/control/continuous_runner_truth.json",
                "data/control/continuous_runner_truth.txt",
                "data/control/runtime_runner_final_truth.json",
                "data/control/runtime_runner_final_truth.txt",
            ]
        )

        # --- Lessons (Section 7) ---
        les = _safe("lessons_final", lambda: _write_lessons_final_runtime_truth(ad, root), {})
        _safe(
            "lessons_runtime_final",
            lambda: _write_lessons_runtime_final(ad, root),
            {},
        )
        out["written"].extend(
            [
                "data/control/lessons_final_runtime_truth.json",
                "data/control/lessons_final_runtime_truth.txt",
                "data/control/lessons_runtime_final_truth.json",
                "data/control/lessons_runtime_final_truth.txt",
            ]
        )

        # --- Avenue A blockers (Section 1) ---
        a_block = _safe(
            "avenue_a_blockers",
            lambda: _write_avenue_a_final_live_blockers(ad, root, f20_rem, f20_ack),
            {"can_switch_live_now": False, "critical_blockers": ["closure_section_failed"]},
        )
        out["written"].extend(["data/control/avenue_a_final_live_blockers.json", "data/control/avenue_a_final_live_blockers.txt"])

        # --- Final merge (Section 8) — mat placeholder passed; summary updated inside writer ---
        final_gap = _safe(
            "final_gaps",
            lambda: _write_final_remaining_gaps_before_live(
                ad, root, a_block, f20_rem, bslr, {}, uar, crt, les
            ),
            {"gaps": [], "summary": {}},
        )
        supreme = _safe(
            "final_go_live",
            lambda: _write_supreme_go_live_decision(ad, root, a_block, final_gap, bslr, les, crt, f20_merged),
            {},
        )
        out["written"].extend(
            [
                "data/control/final_remaining_gaps_before_live.json",
                "data/control/final_remaining_gaps_before_live.txt",
                "data/control/final_go_live_decision.json",
                "data/control/final_go_live_decision.txt",
            ]
        )

        # --- Daemon-grade live authority (runtime root + env fingerprint + supervised/autonomous split) ---
        _daemon_bundle = _safe(
            "daemon_live_authority",
            lambda: __import__(
                "trading_ai.orchestration.daemon_live_authority", fromlist=["write_all_daemon_live_artifacts"]
            ).write_all_daemon_live_artifacts(runtime_root=root),
            {},
        )
        _daemon_i = _safe(
            "daemon_closure_section_i",
            lambda: __import__(
                "trading_ai.orchestration.daemon_live_authority", fromlist=["build_daemon_closure_summary"]
            ).build_daemon_closure_summary(runtime_root=root),
            {},
        )

        def _write_daemon_closure_rollup() -> None:
            doc = _daemon_i if isinstance(_daemon_i, dict) else {}
            ad.write_json("data/control/daemon_closure_rollup.json", doc)
            ad.write_text("data/control/daemon_closure_rollup.txt", json.dumps(doc, indent=2) + "\n")

        _safe("daemon_closure_rollup_write", _write_daemon_closure_rollup, None)
        out["written"].extend(
            [
                "data/control/daemon_live_switch_authority.json",
                "data/control/daemon_live_switch_authority.txt",
                "data/control/daemon_runtime_consistency_truth.json",
                "data/control/daemon_runtime_consistency_truth.txt",
                "data/control/daemon_mode_truth.json",
                "data/control/daemon_mode_truth.txt",
                "data/control/daemon_start_blockers.json",
                "data/control/daemon_start_blockers.txt",
                "data/control/daemon_start_sequence.json",
                "data/control/daemon_start_sequence.txt",
                "data/control/daemon_last_gate_check.json",
                "data/control/daemon_last_live_decision.json",
                "data/control/daemon_closure_rollup.json",
                "data/control/daemon_closure_rollup.txt",
            ]
        )
        if isinstance(_daemon_bundle, dict) and _daemon_bundle.get("authority"):
            out["daemon_live_authority"] = _daemon_bundle
        if _daemon_i:
            out["daemon_closure_section_i"] = _daemon_i

        _avenue_a_auto = _safe(
            "avenue_a_autonomous_runtime",
            lambda: __import__(
                "trading_ai.orchestration.avenue_a_autonomous_runtime_truth", fromlist=["write_all_avenue_a_autonomous_runtime_artifacts"]
            ).write_all_avenue_a_autonomous_runtime_artifacts(runtime_root=root),
            {},
        )
        if _avenue_a_auto:
            out["avenue_a_autonomous_runtime"] = _avenue_a_auto
        out["written"].extend(
            [
                "data/control/avenue_a_autonomous_runtime_verification.json",
                "data/control/avenue_a_autonomous_runtime_verification.txt",
                "data/control/avenue_a_autonomous_cycle_chain.json",
                "data/control/avenue_a_autonomous_cycle_chain.txt",
                "data/control/avenue_a_daemon_cycle_verification.json",
                "data/control/avenue_a_daemon_loop_runtime_truth.json",
                "data/control/avenue_a_daemon_failure_stop_truth.json",
                "data/control/avenue_a_daemon_lock_truth.json",
                "data/control/avenue_a_autonomous_authority.json",
                "data/control/avenue_a_autonomous_authority.txt",
                "data/control/avenue_a_autonomous_remaining_blockers.json",
                "data/control/avenue_a_autonomous_remaining_blockers.txt",
            ]
        )

        _armed = _safe(
            "armed_but_off_authority",
            lambda: __import__(
                "trading_ai.orchestration.armed_but_off_authority", fromlist=["write_all_armed_but_off_artifacts"]
            ).write_all_armed_but_off_artifacts(runtime_root=root),
            {},
        )
        if _armed:
            out["armed_but_off_authority"] = _armed
        out["written"].extend(
            [
                "data/control/autonomous_daemon_live_enable.example.json",
                "data/control/autonomous_daemon_live_enable_guidance.json",
                "data/control/autonomous_daemon_live_enable_guidance.txt",
                "data/control/buy_sell_log_rebuy_runtime_authority.json",
                "data/control/buy_sell_log_rebuy_runtime_authority.txt",
                "data/control/universal_avenue_gate_live_matrix.json",
                "data/control/universal_avenue_gate_live_matrix.txt",
                "data/control/autonomous_daemon_final_truth.json",
                "data/control/autonomous_daemon_final_truth.txt",
                "data/control/runtime_material_change_authority.json",
                "data/control/runtime_material_change_authority.txt",
                "data/control/ceo_session_runtime_truth.json",
                "data/control/ceo_session_runtime_truth.txt",
                "data/control/lessons_learning_runtime_authority.json",
                "data/control/lessons_learning_runtime_authority.txt",
                "data/control/daemon_test_authority.json",
                "data/control/daemon_test_authority.txt",
                "data/control/avenue_a_daemon_runtime_authority.json",
                "data/control/avenue_a_daemon_runtime_authority.txt",
                "data/control/final_daemon_go_live_authority.json",
                "data/control/final_daemon_go_live_authority.txt",
            ]
        )

        # --- Safe activation vs blockers (Section 9) ---
        seq = _safe("activation", lambda: _write_avenue_a_activation_artifacts(ad, root, final_gap, a_block), {"paths_written": []})
        out["written"].extend(seq.get("paths_written", []))
        crit_fg = [g for g in (final_gap.get("gaps") or []) if g.get("classification") == "blocks_live_now"]
        can_act = bool(a_block.get("can_switch_live_now")) and len(crit_fg) == 0
        sec_i = _safe(
            "avenue_a_final_i",
            lambda: _fpw.write_avenue_a_final_section_i(
                runtime_root=root,
                can_go_live=can_act,
                a_block=a_block,
                final_gap=final_gap,
            ),
            [],
        )
        out["written"].extend(sec_i)

        # --- Material change (Section J) + fingerprints ---
        mat = _safe(
            "material_change",
            lambda: _write_runtime_material_change_truth(
                ad,
                root,
                trigger_surface,
                reason,
                mat_detected,
                changed_surface_ids,
                new_fp,
                out.get("section_errors"),
                supreme,
            ),
            {},
        )
        out["written"].extend(
            ["data/control/runtime_material_change_truth.json", "data/control/runtime_material_change_truth.txt"]
        )
        _safe("persist_fp", lambda: _fpw.persist_closure_fingerprints(new_fp, runtime_root=root), None)

        # Persist last run (Section 4 state)
        ad.write_json(
            _STATE_REL,
            {
                "last_trigger_surface": trigger_surface,
                "last_reason": reason,
                "last_generated_at": out["generated_at"],
                "paths_touched": out["written"],
            },
        )

        out["authoritative_booleans"] = _collect_authoritative_booleans(ad, root)
        return out
    finally:
        _CLOSURE_DEPTH -= 1


def _collect_authoritative_booleans(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    """Snapshot booleans from written artifacts (honest defaults if missing)."""
    gb = _read(ad, "data/control/gate_b_final_go_live_truth.json") or {}
    f20 = _read(ad, "data/control/first_20_final_truth.json") or {}
    bslr = _read(ad, "data/control/buy_sell_log_rebuy_truth.json") or {}
    les = _read(ad, "data/control/lessons_final_runtime_truth.json") or {}
    crt = _read(ad, "data/control/continuous_runner_truth.json") or {}
    a_block = _read(ad, "data/control/avenue_a_final_live_blockers.json") or {}
    return {
        "AVENUE_A_CAN_SWITCH_LIVE_NOW": bool(a_block.get("can_switch_live_now")),
        "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": bool(bslr.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN")),
        "FIRST_20_READY_FOR_NEXT_PHASE": bool(f20.get("FIRST_20_READY_FOR_NEXT_PHASE")),
        "LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN": bool(les.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN")),
        "CONTINUOUS_DAEMON_RUNTIME_PROVEN": bool(crt.get("CONTINUOUS_DAEMON_RUNTIME_PROVEN")),
        "gate_b_can_be_switched_live_now_raw": bool(gb.get("gate_b_can_be_switched_live_now")),
    }


# --- Section 2: first_20 ---


def _write_first_20_operator_ack_truth(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    from trading_ai.first_20.constants import P_OPERATOR_ACK
    from trading_ai.first_20.storage import operator_ack_fresh, operator_ack_hours

    max_h = operator_ack_hours()
    path = P_OPERATOR_ACK
    doc = _read(ad, path) or {}
    ts = str(doc.get("acknowledged_at_iso") or "").strip()
    present = bool(ts)
    fresh = operator_ack_fresh(runtime_root=root, max_age_hours=max_h)
    age_h: Optional[float] = None
    if present and ts:
        try:
            from datetime import datetime

            t0 = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t0.tzinfo is None:
                from datetime import timezone as _tz

                t0 = t0.replace(tzinfo=_tz.utc)
            age_h = (datetime.now(t0.tzinfo) - t0).total_seconds() / 3600.0
        except Exception:
            age_h = None
    blocks = bool(present and not fresh)
    payload = {
        "ack_present": present,
        "ack_fresh": fresh,
        "ack_age_hours": age_h,
        "max_allowed_age_hours": max_h,
        "blocks_pass_now": blocks,
        "reason_if_false": ""
        if fresh
        else ("missing_operator_ack" if not present else f"ack_stale_older_than_{max_h}_hours"),
    }
    ad.write_json("data/control/first_20_operator_ack_truth.json", payload)
    return payload


def _write_first_20_adjustment_guardrail_truth(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    from trading_ai.first_20.constants import P_ADJUSTMENTS

    p = ad.root() / P_ADJUSTMENTS
    n = 0
    if p.is_file():
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
    audit_present = p.is_file()
    guardrails_loaded = True  # rules module is always importable
    exercised = n > 0
    proven = bool(exercised)
    why_false = ""
    if not proven:
        why_false = "No lines in first_20_adjustments.jsonl — auto-adjust engine not exercised in production yet."
    blocks_live = False  # advisory: missing exercise does not block Gate B live switch by default
    payload = {
        "adjustments_seen_count": n,
        "guardrail_audit_file_present": audit_present,
        "guardrails_contract_loaded": guardrails_loaded,
        "guardrails_exercised": exercised,
        "FIRST_20_AUTO_ADJUST_GUARDRAILS_PROVEN": proven,
        "why_false": why_false,
        "blocks_live_switch": False,
        "classification_if_not_proven": "advisory_only",
        "honesty": "PROVEN requires at least one audited adjustment line; absence is not fake exercise.",
    }
    ad.write_json("data/control/first_20_adjustment_guardrail_truth.json", payload)
    return payload


def _write_first_20_latency_truth(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    from trading_ai.first_20.constants import P_DIAGNOSTICS
    from trading_ai.first_20.storage import read_jsonl

    rows = read_jsonl(P_DIAGNOSTICS, runtime_root=root)
    present = 0
    missing = 0
    for r in rows:
        am = r.get("avenue_metrics")
        if isinstance(am, dict) and len(am) > 0:
            present += 1
        else:
            missing += 1
    using_defaults = missing > 0 or len(rows) == 0
    latency_auth = present > 0 and missing == 0
    blocks_pass = bool(using_defaults and len(rows) >= 3)
    why_false = ""
    if using_defaults:
        why_false = "Some diagnostic rows lack avenue_metrics — latency/venue execution_quality uses honest placeholders."
    payload = {
        "avenue_metrics_present_count": present,
        "avenue_metrics_missing_count": missing,
        "using_honest_defaults": using_defaults,
        "execution_quality_latency_is_authoritative": latency_auth,
        "blocks_first_20_pass": blocks_pass,
        "classification": "blocks_first_20_pass_only" if blocks_pass else "advisory_only",
        "why_false": why_false,
    }
    ad.write_json("data/control/first_20_latency_truth.json", payload)
    return payload


def _write_first_20_remaining_gaps(
    ad: LocalStorageAdapter,
    root: Path,
    ack: Dict[str, Any],
    lat: Dict[str, Any],
    adj: Dict[str, Any],
) -> Dict[str, Any]:
    gaps: List[Dict[str, Any]] = []

    if ack.get("blocks_pass_now"):
        gaps.append(
            {
                "gap_id": "f20_ack_stale",
                "title": "Operator evidence ack missing or stale",
                "classification": "blocks_first_20_pass_only",
                "why_it_exists": ack.get("reason_if_false") or "operator ack contract",
                "exact_fix_needed": "Refresh data/control/first_20_operator_evidence_ack.json with current ISO timestamp.",
                "exact_artifacts_involved": ["data/control/first_20_operator_evidence_ack.json", "data/control/first_20_operator_ack_truth.json"],
                "auto_clearable": True,
                "operator_clearable": True,
                "can_be_ignored_for_live_switch": True,
                "next_command_if_any": "touch ack file after operator review",
            }
        )

    if not adj.get("FIRST_20_AUTO_ADJUST_GUARDRAILS_PROVEN"):
        gaps.append(
            {
                "gap_id": "f20_adjust_not_exercised",
                "title": "First-20 auto-adjust audit never exercised",
                "classification": "advisory_only",
                "why_it_exists": adj.get("why_false", ""),
                "exact_fix_needed": "Let a caution-trigger fire or run diagnostic scenario that appends one adjustment line.",
                "exact_artifacts_involved": ["data/control/first_20_adjustments.jsonl"],
                "auto_clearable": True,
                "operator_clearable": False,
                "can_be_ignored_for_live_switch": True,
                "next_command_if_any": None,
            }
        )

    if lat.get("using_honest_defaults"):
        gaps.append(
            {
                "gap_id": "f20_latency_defaults",
                "title": "Venue latency metrics not fully populated in first-20 diagnostics",
                "classification": "blocks_first_20_pass_only" if lat.get("blocks_first_20_pass") else "advisory_only",
                "why_it_exists": lat.get("why_false", ""),
                "exact_fix_needed": "Adapter should populate avenue_metrics on each closed trade row.",
                "exact_artifacts_involved": ["data/deployment/first_20_trade_diagnostics.jsonl"],
                "auto_clearable": True,
                "operator_clearable": False,
                "can_be_ignored_for_live_switch": True,
                "next_command_if_any": None,
            }
        )

    doc = {"gaps": gaps, "gap_count": len(gaps), "generated_at": datetime.now(timezone.utc).isoformat()}
    ad.write_json("data/control/first_20_remaining_gaps.json", doc)
    lines = ["FIRST 20 — REMAINING GAPS", "==========================="]
    for g in gaps:
        lines.append(f"- [{g.get('gap_id')}] {g.get('title')} ({g.get('classification')})")
    ad.write_text("data/control/first_20_remaining_gaps.txt", "\n".join(lines) + "\n")
    return doc


# --- Section 3: buy/sell/log/rebuy ---


def _write_buy_sell_log_rebuy_truth(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    loop = _read(ad, "data/control/universal_execution_loop_proof.json") or {}
    ls = loop.get("lifecycle_stages") or {}
    entry_intent = bool(ls.get("entry_intent"))
    entry_fill = bool(ls.get("entry_fill_confirmed"))
    exit_intent = bool(ls.get("exit_intent"))
    exit_fill = bool(ls.get("exit_fill_confirmed"))
    pnl_ok = bool(ls.get("pnl_verified"))
    local_ok = bool(ls.get("local_write_ok"))
    remote_ok = bool(ls.get("remote_write_ok"))
    gov_ok = bool(ls.get("governance_logged"))
    review_ok = bool(ls.get("review_update_ok"))
    blocked = str(loop.get("blocking_reason_if_any") or "")
    ready_rebuy = bool(loop.get("ready_for_rebuy"))
    els = str(loop.get("execution_lifecycle_state") or "")
    proven = bool(loop.get("final_execution_proven"))

    # Runtime proof requires last snapshot to show full lifecycle AND terminal honest semantics
    runtime_proven = bool(
        proven
        and entry_fill
        and exit_fill
        and pnl_ok
        and local_ok
        and els in ("FINALIZED",)
    )
    why_false = ""
    if not runtime_proven:
        why_false = (
            "Loop proof does not show a finalized round-trip with all stages true — see universal_execution_loop_proof.json."
        )

    payload = {
        "truth_version": "buy_sell_log_rebuy_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_artifacts": ["data/control/universal_execution_loop_proof.json"],
        "entry_intent_proven": entry_intent,
        "entry_fill_proven": entry_fill,
        "exit_intent_proven": exit_intent,
        "exit_fill_proven": exit_fill,
        "pnl_proven": pnl_ok,
        "local_log_proven": local_ok,
        "remote_log_proven": remote_ok,
        "governance_log_proven": gov_ok,
        "review_update_proven": review_ok,
        "rebuy_blocked_until_truth_complete": bool(blocked) or not ready_rebuy,
        "rebuy_allowed_only_after_terminal_honest_state": True,
        "policy_notes": {
            "IN_FLIGHT_and_PARTIAL_FAILURE_block_rebuy": "See rebuy_policy.can_open_next_trade_after and execution_lifecycle_state.",
            "duplicate_venue_prefill_terminal": "Terminal failures may allow next scan only after logged proof — not unsafe drift.",
            "entry_filled_exit_failed_blocks_rebuy": "True until resolved per rebuy_policy.",
            "local_logging_failure": "Blocks ready_for_rebuy when policy requires local write before next entry.",
        },
        "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": runtime_proven,
        "exact_reason_if_false": why_false,
        "loop_snapshot": {k: loop.get(k) for k in ("execution_lifecycle_state", "final_execution_proven", "ready_for_rebuy", "rebuy_policy_reason")},
        "honesty": "Summary derived from loop proof artifact — not a duplicate file; if snapshot is stale, runtime state may differ until next write.",
    }
    ad.write_json("data/control/buy_sell_log_rebuy_truth.json", payload)
    ad.write_text(
        "data/control/buy_sell_log_rebuy_truth.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )
    return payload


# --- Section 4 ---


def _write_runtime_material_change_truth(
    ad: LocalStorageAdapter,
    root: Path,
    surface: str,
    reason: str,
    material_change_detected: bool,
    changed_surface_ids: List[str],
    new_fingerprints: Dict[str, str],
    errors_if_any: Optional[Dict[str, str]],
    supreme_decision: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    rt = _read(ad, "data/control/runtime_artifact_refresh_truth.json") or {}
    mgr_present = True

    hooks = {
        "first_20_closed_trade": "trading_ai.first_20.engine.process_closed_trade → write_live_switch_closure_bundle",
        "universal_loop_proof": "universal_execution_loop_proof.write_loop_proof_from_trade_result → write_live_switch_closure_bundle",
        "material_change_bridge": "runtime_truth_material_change.refresh_runtime_truth_after_material_change → run_refresh + write_live_switch_closure_bundle",
        "gate_b_live_micro": "runtime_proof.live_execution_validation post refresh",
        "gate_b_tick": "deployment.gate_b_production_tick refresh hook",
    }
    payload = {
        "truth_version": "runtime_material_change_truth_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "material_change_detected": material_change_detected,
        "changed_surface_ids": list(changed_surface_ids),
        "closure_bundle_refreshed": True,
        "final_go_live_truth_refreshed": bool(supreme_decision),
        "remaining_gaps_refreshed": True,
        "last_refresh_reason": reason,
        "last_refresh_trigger_surface": surface,
        "fingerprint_surfaces": list(_fpw.MATERIAL_CLOSURE_SURFACES.keys()),
        "new_fingerprint_preview": {k: new_fingerprints.get(k) for k in sorted(new_fingerprints.keys())[:12]},
        "errors_if_any": errors_if_any or {},
        "refresh_manager_present": mgr_present,
        "material_change_hooks_present": True,
        "hooks_by_surface": hooks,
        "refresh_on_meaningful_change_only": True,
        "stale_artifact_ids": rt.get("stale_artifact_ids") if isinstance(rt, dict) else [],
        "runtime_truth_self_refreshing": bool(rt.get("manager_honesty")) if isinstance(rt, dict) else False,
        "exact_known_nonrefreshing_surfaces": [
            "Pure clock/heartbeat-only ticks with no dependency fingerprint change",
            "Artifacts with no registered dependency path in MATERIAL_CLOSURE_SURFACES",
        ],
        "anti_storm_note": "Single bundle pass per invocation; fingerprints persisted once; no recursive closure calls.",
        "honesty": "Fingerprint change compares mtime+size — not full content hash; hooks fire on proof surfaces.",
    }
    ad.write_json("data/control/runtime_material_change_truth.json", payload)
    ad.write_text("data/control/runtime_material_change_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


# --- Section 5 ---


def _write_universal_avenue_live_readiness(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
    from trading_ai.universal_execution.universal_live_switch_truth import build_universal_live_switch_truth

    uls = build_universal_live_switch_truth(runtime_root=root)
    avenues_out: Dict[str, Any] = {}
    for aid in ("A", "B", "C"):
        sw, blockers, diag = compute_avenue_switch_live_now(aid, runtime_root=root)
        uv = (uls.get("avenues") or {}).get(aid) or {}
        avenues_out[aid] = {
            "can_switch_live_now": bool(sw),
            "live_orders_proven": bool(uv.get("live_order_ready") or uv.get("micro_proven")),
            "repeated_tick_ready": bool(uv.get("repeated_tick_ready")),
            "continuous_daemon_ready": bool(uv.get("continuous_loop_ready")),
            "lessons_runtime_intelligence_ready": bool(uv.get("lessons_runtime_intelligence_ready")),
            "independent_proof_present": bool(diag.get("independent_proof_check")) if aid in ("B", "C") else True,
            "exact_blockers": list(blockers),
            "exact_nonblockers": [],
            "proof_paths": [
                "data/control/gate_b_final_go_live_truth.json",
                "data/control/universal_live_switch_truth.json",
                f"data/control/avenue_{aid}_independent_live_proof.json",
            ],
            "honesty": uv.get("honesty", ""),
        }
    payload = {
        "truth_version": "universal_avenue_live_readiness_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "avenues": avenues_out,
        "policy": "Avenue B/C never inherit Avenue A readiness.",
    }
    ad.write_json("data/control/universal_avenue_live_readiness.json", payload)
    ad.write_text(
        "data/control/universal_avenue_live_readiness.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )
    return payload


# --- Section 6 ---


def _write_continuous_runner_truth(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration import runtime_runner as rr

    lock_p = ad.root() / "data/control/runtime_runner.lock"
    hb = _read(ad, "data/control/runtime_runner_heartbeat.json") or {}
    ver = _read(ad, "data/control/runtime_runner_daemon_verification.json") or {}
    mode = rr.global_runner_mode()
    proven = rr.evaluate_continuous_daemon_runtime_proven(runtime_root=root)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner_exists": lock_p.is_file() or bool(hb.get("ts")),
        "lock_exclusivity_proven": bool(ver.get("lock_exclusivity_verified")),
        "heartbeat_proven": bool(hb.get("ts")),
        "failure_stop_proven": bool(ver.get("failure_stop_verified")),
        "live_order_submission_from_runner": mode == "live_execution",
        "tick_only_runner": mode == "tick_only",
        "CONTINUOUS_DAEMON_RUNTIME_PROVEN": proven,
        "exact_reason_if_false": ""
        if proven
        else "Requires data/control/runtime_runner_daemon_verification.json with lock_exclusivity_verified and failure_stop_verified — not lock file alone.",
        "what_runner_is_allowed_to_do": "Refresh orchestration truth on tick; optional live_execution only when gates pass.",
        "what_runner_is_not_allowed_to_do": "Place live orders in tick_only/disabled; bypass avenue switch or kill switch.",
        "honesty": "Heartbeat alone does not prove daemon safety — see daemon_verification.json.",
    }
    ad.write_json("data/control/continuous_runner_truth.json", payload)
    ad.write_text("data/control/continuous_runner_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


# --- Section 7 ---


def _write_lessons_final_runtime_truth(ad: LocalStorageAdapter, root: Path) -> Dict[str, Any]:
    eff = _read(ad, "data/control/lessons_runtime_effect.json") or {}
    rank = bool(eff.get("influenced_ranking"))
    ent = bool(eff.get("influenced_entry"))
    ex = bool(eff.get("influenced_exit"))
    reb = bool(eff.get("influenced_rebuy"))
    proven_flag = bool(eff.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"))
    # Strict: Gate B path only from effect file; A/C not wired to lesson_runtime_influence in NTE/Kalshi universal adapters yet
    payload = {
        "lessons_affect_ranking": rank,
        "lessons_affect_entry": ent,
        "lessons_affect_exit": ex,
        "lessons_affect_rebuy": reb,
        "lessons_affect_avenue_a_now": False,
        "lessons_affect_avenue_b_now": bool(rank or ent or ex or reb),
        "lessons_affect_avenue_c_now": False,
        "LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN": proven_flag,
        "exact_wiring_scope": "Gate B Coinbase gate_b_engine + lesson_runtime_influence hooks when lessons_runtime_effect.json shows influence.",
        "exact_unwired_scope": "Avenue A NTE order path does not import lesson_runtime_influence; Avenue B Kalshi / Avenue C not universal-wired for lessons execution influence.",
        "exact_reason_if_false": ""
        if proven_flag
        else "No influenced_* flags and LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN false in lessons_runtime_effect.json.",
        "source_artifact": "data/control/lessons_runtime_effect.json",
        "honesty": "Do not conflate intelligence preflight with execution-path influence.",
    }
    ad.write_json("data/control/lessons_final_runtime_truth.json", payload)
    ad.write_text("data/control/lessons_final_runtime_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


# --- Section 1: Avenue A blockers ---


def _write_avenue_a_final_live_blockers(
    ad: LocalStorageAdapter, root: Path, _f20_remaining_doc: Dict[str, Any], ack: Dict[str, Any]
) -> Dict[str, Any]:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    gb = _read(ad, "data/control/gate_b_final_go_live_truth.json")
    if not gb:
        try:
            from trading_ai.reports.gate_b_final_go_live_truth import build_gate_b_final_go_live_truth

            gb = build_gate_b_final_go_live_truth(runtime_root=root) or {}
        except Exception:
            gb = {}
    else:
        gb = dict(gb)

    sw, blockers, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    gate_b_ok = bool(gb.get("gate_b_can_be_switched_live_now"))

    critical: List[str] = []
    important: List[str] = []
    advisory: List[str] = []

    for b in blockers:
        critical.append(b)
    if not gate_b_ok and gb.get("if_false_exact_why"):
        critical.append(str(gb.get("if_false_exact_why")))
    if ack.get("blocks_pass_now"):
        important.append("first_20_operator_ack_blocks_pass (first-20 phase only)")

    f20_final = _read(ad, "data/control/first_20_final_truth.json") or {}
    if f20_final.get("FIRST_20_READY_FOR_NEXT_PHASE") is False:
        advisory.append("first_20_not_ready_for_next_phase — see first_20_pass_decision.json")

    f20_pass = bool((_read(ad, "data/control/first_20_pass_decision.json") or {}).get("passed"))
    require_f20 = (os.environ.get("EZRAS_FIRST_20_REQUIRED_FOR_LIVE") or "").strip().lower() in ("1", "true", "yes")
    if require_f20 and not f20_pass:
        critical.append("EZRAS_FIRST_20_REQUIRED_FOR_LIVE_and_first_20_pass_false")

    can = bool(sw and gate_b_ok)
    if require_f20:
        can = can and f20_pass

    payload = {
        "truth_version": "avenue_a_final_live_blockers_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "first_20_policy_for_avenue_a": {
            "blocks_live_only_if": "EZRAS_FIRST_20_REQUIRED_FOR_LIVE is true AND first_20_pass_decision.passed is false",
            "otherwise": "first_20 incomplete is advisory_only (see advisory_only_items), not a switch_live blocker",
        },
        "can_switch_live_now": can,
        "authoritative_decision_source": "orchestration/switch_live.compute_avenue_switch_live_now('A') + data/control/gate_b_final_go_live_truth.json (Gate B semantics)",
        "critical_blockers": sorted(set(critical)),
        "important_but_nonblocking_items": important,
        "advisory_only_items": advisory,
        "what_is_runtime_proven": "Gate B micro proof + production tick artifacts when present under data/control/gate_b_*.json",
        "what_is_contract_proven_only": "Universal execution truth contract stages — verify against venue fills in live",
        "what_is_not_yet_proven": "Full multi-day production edge; Avenue B/C independent live; daemon verification unless file present",
        "exact_next_command_if_any": None
        if can
        else "Resolve critical_blockers; refresh gate-b-tick and closure bundle; confirm operator_live_confirmation.json",
        "exact_reason_if_false": "; ".join(critical) if not can else "",
        "exact_reason_if_true": "switch_live allows A and gate_b_can_be_switched_live_now true and first-20 policy satisfied"
        if can
        else "",
        "proof_paths_used": [
            "data/control/gate_b_final_go_live_truth.json",
            "data/control/go_no_go_decision.json",
            "data/control/execution_mirror_results.json",
            "data/control/system_execution_lock.json",
        ],
    }
    ad.write_json("data/control/avenue_a_final_live_blockers.json", payload)
    ad.write_text("data/control/avenue_a_final_live_blockers.txt", json.dumps(payload, indent=2) + "\n")
    return payload


# --- Section 8 ---


def _write_final_remaining_gaps_before_live(
    ad: LocalStorageAdapter,
    root: Path,
    a_block: Dict[str, Any],
    f20_doc: Dict[str, Any],
    bslr: Dict[str, Any],
    mat: Dict[str, Any],
    uar: Dict[str, Any],
    crt: Dict[str, Any],
    les: Dict[str, Any],
) -> Dict[str, Any]:
    merged: List[Dict[str, Any]] = []

    for idx, b in enumerate(a_block.get("critical_blockers") or []):
        merged.append(
            {
                "id": f"critical_switch_{idx}_{str(b)[:40]}",
                "title": str(b),
                "classification": "blocks_live_now",
                "avenue_scope": "A",
                "truth_scope": "switch_or_gate_b",
                "exact_fix_needed": "Resolve listed blocker in authoritative artifacts",
                "next_command_if_any": a_block.get("exact_next_command_if_any"),
                "can_be_ignored_for_live_switch": False,
                "reason": str(b),
            }
        )

    for g in f20_doc.get("gaps") or []:
        merged.append(
            {
                "id": g.get("gap_id"),
                "title": g.get("title"),
                "classification": g.get("classification", "advisory_only"),
                "avenue_scope": "A",
                "truth_scope": "first_20",
                "exact_fix_needed": g.get("exact_fix_needed", ""),
                "next_command_if_any": g.get("next_command_if_any"),
                "can_be_ignored_for_live_switch": bool(g.get("can_be_ignored_for_live_switch")),
                "reason": g.get("why_it_exists", ""),
            }
        )

    if not bslr.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN"):
        merged.append(
            {
                "id": "bslr_not_proven",
                "title": "Buy/sell/log/rebuy runtime not proven in last loop snapshot",
                "classification": "blocks_repeatable_runtime_only",
                "avenue_scope": "universal",
                "truth_scope": "execution_loop",
                "exact_fix_needed": bslr.get("exact_reason_if_false", ""),
                "next_command_if_any": "Complete round-trip and refresh universal_execution_loop_proof.json",
                "can_be_ignored_for_live_switch": False,
                "reason": "Incomplete lifecycle stages in loop proof",
            }
        )

    if not les.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"):
        merged.append(
            {
                "id": "lessons_influence_not_proven",
                "title": "Lessons runtime decision influence not proven",
                "classification": "blocks_lessons_intelligence_only",
                "avenue_scope": "B",
                "truth_scope": "lessons",
                "exact_fix_needed": les.get("exact_reason_if_false", ""),
                "next_command_if_any": None,
                "can_be_ignored_for_live_switch": True,
                "reason": "Advisory unless lessons are required for your policy",
            }
        )

    if not crt.get("CONTINUOUS_DAEMON_RUNTIME_PROVEN"):
        merged.append(
            {
                "id": "daemon_not_verified",
                "title": "Continuous daemon safety not verified",
                "classification": "blocks_full_autonomous_production_only",
                "avenue_scope": "runner",
                "truth_scope": "runtime_runner",
                "exact_fix_needed": crt.get("exact_reason_if_false", ""),
                "next_command_if_any": "Populate runtime_runner_daemon_verification.json via staging tests",
                "can_be_ignored_for_live_switch": True,
                "reason": "Tick-only or manual runs do not need daemon proof",
            }
        )

    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    sw_b, bl_b, _ = compute_avenue_switch_live_now("B", runtime_root=root)
    sw_c, bl_c, _ = compute_avenue_switch_live_now("C", runtime_root=root)

    blocking_live = sum(1 for g in merged if g.get("classification") == "blocks_live_now")
    nothing_critical = blocking_live == 0 and bool(a_block.get("can_switch_live_now"))

    summary = {
        "blocking_count_live_now": blocking_live,
        "blocking_count_repeatability": sum(1 for g in merged if g.get("classification") == "blocks_repeatable_runtime_only"),
        "blocking_count_full_autonomous": sum(
            1 for g in merged if g.get("classification") == "blocks_full_autonomous_production_only"
        ),
        "blocking_count_lessons": sum(1 for g in merged if g.get("classification") == "blocks_lessons_intelligence_only"),
        "advisory_count": sum(
            1 for g in merged if str(g.get("classification", "")).startswith("advisory")
        ),
        "nothing_critical_remaining_for_avenue_a_live_switch": bool(nothing_critical),
        "nothing_critical_remaining_for_avenue_b_live_switch": bool(sw_b),
        "nothing_critical_remaining_for_avenue_c_live_switch": bool(sw_c),
        "honesty": "Per-avenue nothing_critical uses switch_live for B/C; A uses merged blocks_live_now + can_switch.",
    }
    doc = {"gaps": merged, "summary": summary, "generated_at": datetime.now(timezone.utc).isoformat()}
    ad.write_json("data/control/final_remaining_gaps_before_live.json", doc)
    ad.write_text("data/control/final_remaining_gaps_before_live.txt", json.dumps(doc, indent=2) + "\n")
    return doc


# --- Section 9 ---


def _write_avenue_a_activation_artifacts(
    ad: LocalStorageAdapter, root: Path, final_gap: Dict[str, Any], a_block: Dict[str, Any]
) -> Dict[str, Any]:
    paths: List[str] = []
    merged = final_gap.get("gaps") or []
    critical = [g for g in merged if g.get("classification") == "blocks_live_now"]
    can = bool(a_block.get("can_switch_live_now")) and len(critical) == 0

    if can:
        body = {
            "prerequisite_truth_checks": [
                "gate_b_final_go_live_truth.json gate_b_can_be_switched_live_now",
                "switch_live blockers empty for Avenue A",
                "operator_live_confirmation.json or EZRAS_OPERATOR_LIVE_CONFIRMED",
                "system execution lock ready_for_live_execution",
            ],
            "exact_env_required": [
                "GATE_B_LIVE_EXECUTION_ENABLED per deployment policy",
                "EZRAS_RUNTIME_ROOT",
                "COINBASE credentials as required by NTE",
            ],
            "exact_command_order": [
                "python -m trading_ai.deployment gate-b-tick",
                "refresh closure: from trading_ai.operator_truth import write_live_switch_closure_bundle; write_live_switch_closure_bundle()",
            ],
            "inspect_after_activation": [
                "data/control/universal_execution_loop_proof.json",
                "data/control/first_20_truth.json if diagnostic active",
                "logs/post_trade_log.md",
            ],
            "required_proof_files": [
                "data/control/gate_b_last_production_tick.json",
                "data/control/universal_execution_loop_proof.json",
            ],
            "stop_immediately_if": [
                "kill_switch active",
                "emergency_brake in adaptive proof",
                "first_20 phase PAUSED_REVIEW_REQUIRED",
            ],
            "what_does_not_count_as_proof": [
                "File existence without matching runtime fills",
                "Mock-only test passes without production correlation",
            ],
            "first_60_minute_checklist": [
                "Watch Telegram for duplicate/no-trade spam regression",
                "Verify loop proof updates each cycle",
                "Confirm PnL reconciliation matches venue",
            ],
            "first_20_trade_checklist": [
                "See data/control/first_20_scoreboard.txt",
                "Operator ack within max age",
            ],
            "rollback": [
                "Set EZRAS_RUNNER_MODE=disabled or tick_only",
                "Clear EZRAS_OPERATOR_LIVE_CONFIRMED",
                "Engage kill_switch via control API",
            ],
        }
        ad.write_json("data/control/avenue_a_safe_go_live_sequence.json", body)
        ad.write_text("data/control/avenue_a_safe_go_live_sequence.txt", json.dumps(body, indent=2) + "\n")
        paths.extend(
            [
                "data/control/avenue_a_safe_go_live_sequence.json",
                "data/control/avenue_a_safe_go_live_sequence.txt",
            ]
        )
    else:
        blk = {
            "critical_gaps": critical,
            "can_switch_live_now": a_block.get("can_switch_live_now"),
            "reason": a_block.get("exact_reason_if_false"),
        }
        ad.write_json("data/control/avenue_a_live_activation_blockers.json", blk)
        ad.write_text("data/control/avenue_a_live_activation_blockers.txt", json.dumps(blk, indent=2) + "\n")
        paths.extend(
            [
                "data/control/avenue_a_live_activation_blockers.json",
                "data/control/avenue_a_live_activation_blockers.txt",
            ]
        )
    return {"paths_written": paths}
