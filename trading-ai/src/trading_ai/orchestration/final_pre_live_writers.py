"""
Final pre-live closure writers — gap sweep, certifications, fingerprints, supreme decision.

Honest defaults: absence of proof files yields false / DO_NOT_GO_LIVE.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.storage.storage_adapter import LocalStorageAdapter

# Surface id -> dependency paths (relative to runtime root) for material-change detection
MATERIAL_CLOSURE_SURFACES: Dict[str, List[str]] = {
    "first_20_truth": [
        "data/control/first_20_truth.json",
        "data/deployment/first_20_trade_diagnostics.jsonl",
        "data/control/first_20_pass_decision.json",
        "data/control/first_20_rebuy_audit.json",
    ],
    "universal_loop_proof": [
        "data/control/universal_execution_loop_proof.json",
    ],
    "gate_b_tick_truth": [
        "data/control/gate_b_last_production_tick.json",
        "data/control/gate_b_final_go_live_truth.json",
    ],
    "gate_b_live_proof": [
        "execution_proof/gate_b_live_execution_validation.json",
    ],
    "lessons_runtime_effect": [
        "data/control/lessons_runtime_effect.json",
    ],
    "runner_truth": [
        "data/control/runtime_runner_heartbeat.json",
        "data/control/runtime_runner_daemon_verification.json",
        "data/control/runtime_runner_health.json",
    ],
    "avenue_readiness": [
        "data/control/system_execution_lock.json",
        "data/control/operator_live_confirmation.json",
        "data/control/go_no_go_decision.json",
        "data/control/execution_mirror_results.json",
        "data/control/avenue_B_independent_live_proof.json",
        "data/control/avenue_C_independent_live_proof.json",
    ],
    "final_go_live": [
        "data/control/final_go_live_decision.json",
    ],
    "daemon_live_authority": [
        "data/control/daemon_live_switch_authority.json",
        "data/control/daemon_runtime_consistency_truth.json",
        "data/control/gate_b_global_halt_truth.json",
        "data/control/operator_live_confirmation.json",
    ],
    "autonomous_daemon_live_enable": [
        "data/control/autonomous_daemon_live_enable.json",
        "data/control/autonomous_daemon_live_enable.example.json",
    ],
    "armed_but_off_bundle": [
        "data/control/final_daemon_go_live_authority.json",
        "data/control/autonomous_daemon_final_truth.json",
        "data/control/universal_avenue_gate_live_matrix.json",
    ],
}

_FP_STATE = "data/control/_material_closure_fingerprints.json"


def _fp_one_file(ad: LocalStorageAdapter, rel: str) -> str:
    p = ad.root() / rel
    if not p.is_file():
        return "missing"
    try:
        st = p.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "missing"


def fingerprint_closure_surfaces(*, runtime_root: Path) -> Dict[str, str]:
    """Per-surface aggregate fingerprint (not full file hash — fast, mtime+size)."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    out: Dict[str, str] = {}
    for sid, rels in sorted(MATERIAL_CLOSURE_SURFACES.items()):
        h = hashlib.sha256()
        for rel in sorted(rels):
            h.update(rel.encode())
            h.update(_fp_one_file(ad, rel).encode())
        out[sid] = h.hexdigest()[:40]
    return out


def detect_material_closure_change(
    *,
    runtime_root: Path,
) -> Tuple[bool, List[str], Dict[str, str], Dict[str, str]]:
    """
    Compare current surface fingerprints to last persisted bundle run.
    Returns (material_change_detected, changed_surface_ids, old_fps, new_fps).
    """
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    new_fps = fingerprint_closure_surfaces(runtime_root=runtime_root)
    old_raw = ad.read_json(_FP_STATE) or {}
    old_fps = old_raw.get("fingerprints") if isinstance(old_raw.get("fingerprints"), dict) else {}
    changed = [k for k in sorted(new_fps.keys()) if old_fps.get(k) != new_fps.get(k)]
    # First run: treat as material change so operators see a fresh delta
    first = not old_fps
    material = bool(changed) or first
    return material, changed, dict(old_fps), new_fps


def persist_closure_fingerprints(new_fps: Dict[str, str], *, runtime_root: Path) -> None:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.write_json(
        _FP_STATE,
        {
            "fingerprints": new_fps,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def build_final_system_gap_sweep(*, runtime_root: Path) -> Dict[str, Any]:
    """Static + runtime gap list — does not assert green."""
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    gaps: List[Dict[str, Any]] = []

    def _add(
        gid: str,
        title: str,
        severity: str,
        subsystem: str,
        reason: str,
        fix: str,
        auto: bool,
        cmd: Optional[str],
    ) -> None:
        gaps.append(
            {
                "gap_id": gid,
                "title": title,
                "severity": severity,
                "exact_file_or_subsystem": subsystem,
                "exact_reason": reason,
                "exact_fix_needed": fix,
                "can_auto_fix": auto,
                "next_command_if_any": cmd,
            }
        )

    if not ad.exists("data/control/universal_execution_loop_proof.json"):
        _add(
            "GAP-UE-001",
            "No universal execution loop proof snapshot",
            "blocks_operator_confidence",
            "data/control/universal_execution_loop_proof.json",
            "File absent — no finalized buy→sell→log→rebuy runtime snapshot in this root.",
            "Complete a supervised round-trip that writes the loop proof via write_loop_proof_from_trade_result.",
            False,
            None,
        )

    sw_a, bl_a, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    if not sw_a:
        _add(
            "GAP-LIVE-A-001",
            "Avenue A live switch false",
            "blocks_live_now",
            "trading_ai/orchestration/switch_live.py",
            "; ".join(bl_a) if bl_a else "switch_live returned false",
            "Resolve blockers (operator confirmation, go/no-go, mirror, locks) per switch_live diagnostics.",
            False,
            None,
        )

    sw_b, bl_b, _ = compute_avenue_switch_live_now("B", runtime_root=root)
    if not sw_b:
        _add(
            "GAP-LIVE-B-001",
            "Avenue B (Kalshi) not independently live-proven",
            "blocks_live_now",
            "data/control/avenue_B_independent_live_proof.json + gate_b",
            "; ".join(bl_b) if bl_b else "independent proof missing",
            "Complete avenue_B_independent_live_proof.json with validated_by_operator; enable gate_b in lock.",
            False,
            None,
        )

    sw_c, bl_c, _ = compute_avenue_switch_live_now("C", runtime_root=root)
    if not sw_c:
        _add(
            "GAP-LIVE-C-001",
            "Avenue C (Tastytrade) scaffold — execution not wired",
            "blocks_live_now",
            "multi_avenue/avenue_registry.py + switch_live",
            "; ".join(bl_c) if bl_c else "scaffold",
            "Wire Tastytrade execution + independent proof — no auto green.",
            False,
            None,
        )

    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    if not (ver.get("lock_exclusivity_verified") and ver.get("failure_stop_verified")):
        _add(
            "GAP-DAEMON-001",
            "Daemon verification file incomplete",
            "advisory_only",
            "data/control/runtime_runner_daemon_verification.json",
            "Unattended daemon safety not proven (tests/staging verification missing).",
            "Run tests/test_runtime_runner_safety.py flow or staging checklist; write verification JSON.",
            True,
            "pytest trading-ai/tests/test_runtime_runner_safety.py -q",
        )

    eff = ad.read_json("data/control/lessons_runtime_effect.json") or {}
    if not eff.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"):
        _add(
            "GAP-LSN-001",
            "Lessons runtime influence not proven",
            "blocks_lessons_intelligence_only",
            "data/control/lessons_runtime_effect.json",
            "No influenced_* proof in effect file.",
            "Exercise Gate B path with lessons that alter scores; effect writer must set flags.",
            False,
            None,
        )

    if not ad.exists("data/control/gate_b_last_production_tick.json"):
        _add(
            "GAP-GBT-001",
            "Gate B production tick artifact absent",
            "blocks_operator_confidence",
            "data/control/gate_b_last_production_tick.json",
            "No recorded production tick — micro/live tick truth incomplete.",
            "Run deployment gate-b-tick when policy allows.",
            False,
            "python -m trading_ai.deployment gate-b-tick",
        )

    return {
        "truth_version": "final_system_gap_sweep_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gap_count": len(gaps),
        "gaps": gaps,
        "honesty": "This sweep lists common gaps; runtime may have additional context in logs.",
    }


def build_buy_sell_log_rebuy_certification(*, runtime_root: Path) -> Dict[str, Any]:
    """Strict certification — BUY_SELL_LOG_REBUY_RUNTIME_PROVEN per user Section C."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    ls = loop.get("lifecycle_stages") or {}
    flags = list(loop.get("partial_failure_flags") or [])
    remote_req = True
    try:
        bundle = loop.get("bundle") or {}
        rw = bundle.get("remote_write") or {}
        remote_req = bool(rw.get("remote_required", True))
    except Exception:
        pass

    def _ok(name: str) -> bool:
        return bool(ls.get(name)) if isinstance(ls, dict) else False

    entry_fill = _ok("entry_fill_confirmed")
    exit_fill = _ok("exit_fill_confirmed")
    pnl_ok = _ok("pnl_verified")
    local_ok = _ok("local_write_ok")
    remote_ok = _ok("remote_write_ok") or (not remote_req)
    gov_ok = _ok("governance_logged")
    review_ok = _ok("review_update_ok")
    rebuy_gate = bool(loop.get("ready_for_rebuy")) and not flags

    strict_runtime = bool(
        loop.get("final_execution_proven")
        and entry_fill
        and exit_fill
        and pnl_ok
        and local_ok
        and remote_ok
        and gov_ok
        and review_ok
        and not flags
        and str(loop.get("execution_lifecycle_state") or "") == "FINALIZED"
    )

    missing: List[str] = []
    if not entry_fill:
        missing.append("entry_fill_confirmed")
    if not exit_fill:
        missing.append("exit_fill_confirmed")
    if not pnl_ok:
        missing.append("pnl_verified")
    if not local_ok:
        missing.append("local_write_ok")
    if remote_req and not bool(ls.get("remote_write_ok") if isinstance(ls, dict) else False):
        missing.append("remote_write_ok")
    if not gov_ok:
        missing.append("governance_logged")
    if not review_ok:
        missing.append("review_update_ok")
    if flags:
        missing.append("partial_failure_flags")

    why_false = ""
    if not strict_runtime:
        why_false = (
            "Certification requires finalized loop proof with all stages true, no partial_failure_flags, "
            "rebuy ready — see universal_execution_loop_proof.json."
        )

    return {
        "truth_version": "buy_sell_log_rebuy_certification_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_stage_proven": entry_fill,
        "exit_stage_proven": exit_fill,
        "pnl_stage_proven": pnl_ok,
        "local_log_stage_proven": local_ok,
        "remote_log_stage_proven": remote_ok,
        "governance_stage_proven": gov_ok,
        "review_stage_proven": review_ok,
        "rebuy_gate_proven": rebuy_gate,
        "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": strict_runtime,
        "exact_reason_if_false": why_false if not strict_runtime else "",
        "exact_missing_stage_if_any": missing,
        "proof_sources": ["data/control/universal_execution_loop_proof.json"],
        "partial_failure_flags": flags,
    }


def write_first_20_merged_final_truth_files(*, runtime_root: Path) -> Dict[str, Any]:
    """Merge closure supplement into first_20_final_truth without wiping engine fields."""
    from trading_ai.first_20.constants import P_FINAL_JSON, P_FINAL_TXT

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    merged = merge_first_20_closure_final_truth(runtime_root=root)
    existing = ad.read_json(P_FINAL_JSON) or {}
    if not isinstance(existing, dict):
        existing = {}
    out = {**existing, **merged, "closure_bundle_merged_at": merged.get("generated_at")}
    ad.write_json(P_FINAL_JSON, out)
    ad.write_text(P_FINAL_TXT, json.dumps(out, indent=2, default=str) + "\n")
    return out


def merge_first_20_closure_final_truth(*, runtime_root: Path) -> Dict[str, Any]:
    """Merge engine truth + pass + ack for operator-facing first_20_final_truth closure fields."""
    from trading_ai.first_20.constants import P_DIAGNOSTICS, P_OPERATOR_ACK, P_PASS_DECISION, P_TRUTH, PhaseStatus
    from trading_ai.first_20.storage import operator_ack_fresh, operator_ack_hours, read_json, read_jsonl

    root = Path(runtime_root).resolve()
    truth = read_json(P_TRUTH, runtime_root=root) or {}
    pd = read_json(P_PASS_DECISION, runtime_root=root) or {}
    rows = read_jsonl(P_DIAGNOSTICS, runtime_root=root)
    n = len(rows)
    ack_doc = read_json(P_OPERATOR_ACK, runtime_root=root) or {}
    max_h = operator_ack_hours()
    ack_fresh = operator_ack_fresh(runtime_root=root, max_age_hours=max_h)

    passed = bool(pd.get("passed"))
    phase = str(truth.get("phase_status") or "")
    ready_next = bool(truth.get("ready_for_next_phase"))
    safe_capital = bool(
        n >= 1
        and ack_fresh
        and passed is not False
        and phase != PhaseStatus.PAUSED_REVIEW_REQUIRED.value
    )
    if n < 20:
        safe_capital = False

    require_f20 = (os.environ.get("EZRAS_FIRST_20_REQUIRED_FOR_LIVE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    first_20_blocks_avenue_a = bool(require_f20 and not passed)

    payload = {
        "truth_version": "first_20_closure_merged_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "closure_writer": "trading_ai.orchestration.final_pre_live_writers.merge_first_20_closure_final_truth",
        "diagnostic_rows_count": n,
        "operator_ack_fresh": ack_fresh,
        "operator_ack_max_age_hours": max_h,
        "first_20_pass_explicit": passed,
        "FIRST_20_READY_FOR_NEXT_PHASE": ready_next,
        "FIRST_20_SAFE_FOR_LIVE_CAPITAL": safe_capital,
        "FIRST_20_SAFE_FOR_LIVE_CAPITAL_false_reason": ""
        if safe_capital
        else (
            "insufficient_rows_or_ack_or_pass_not_true"
            if n < 20
            else "operator_ack_stale_or_pass_false_or_phase_paused"
        ),
        "first_20_blocks_avenue_a_live": first_20_blocks_avenue_a,
        "first_20_required_for_live_env": require_f20,
        "phase_status": phase,
        "honesty": "Merged view for closure; engine may overwrite P_FINAL_JSON on next closed trade.",
        "engine_truth_snapshot": {k: truth.get(k) for k in list(truth.keys())[:30]},
    }
    return payload


def build_final_avenue_readiness_authority(*, runtime_root: Path) -> Dict[str, Any]:
    """Per-avenue isolation — no cross-avenue inheritance."""
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
    from trading_ai.orchestration.runtime_runner import evaluate_continuous_daemon_runtime_proven
    from trading_ai.universal_execution.universal_live_switch_truth import build_universal_live_switch_truth

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    uls = build_universal_live_switch_truth(runtime_root=root)
    loop_ok = bool((ad.read_json("data/control/universal_execution_loop_proof.json") or {}).get("final_execution_proven"))
    les_ok = bool((ad.read_json("data/control/lessons_runtime_effect.json") or {}).get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"))
    daemon_ok = evaluate_continuous_daemon_runtime_proven(runtime_root=root)
    b_ind = ad.read_json("data/control/avenue_B_independent_live_proof.json") or {}
    c_ind = ad.read_json("data/control/avenue_C_independent_live_proof.json") or {}

    out: Dict[str, Any] = {}
    for aid in ("A", "B", "C"):
        sw, blockers, diag = compute_avenue_switch_live_now(aid, runtime_root=root)
        uv = (uls.get("avenues") or {}).get(aid) or {}
        # Avenue-specific proof paths only (no sibling inheritance)
        proof_sources_map: Dict[str, List[str]] = {
            "A": [
                "data/control/operator_live_confirmation.json",
                "data/control/universal_execution_loop_proof.json",
                "data/control/system_execution_lock.json",
                "execution_proof/ (gate A / NTE as applicable)",
            ],
            "B": [
                "data/control/avenue_B_independent_live_proof.json",
                "data/control/gate_b_final_go_live_truth.json",
                "data/control/system_execution_lock.json (gate_b_enabled)",
            ],
            "C": [
                "data/control/avenue_C_independent_live_proof.json",
                "trading_ai/multi_avenue/avenue_registry.py (wiring_status)",
            ],
        }
        proof_sources = proof_sources_map[aid]

        live_proven_a = bool(loop_ok and sw)
        live_proven_b = bool(
            b_ind.get("independent_live_proven") is True and b_ind.get("validated_by_operator") is True
        )
        live_proven_c = bool(
            c_ind.get("independent_live_proven") is True and c_ind.get("validated_by_operator") is True
        )
        live_map = {"A": live_proven_a, "B": live_proven_b, "C": live_proven_c}

        out[aid] = {
            "venue_mapping": {"A": "Coinbase", "B": "Kalshi", "C": "Tastytrade"}[aid],
            "can_switch_live_now": bool(sw),
            "live_orders_runtime_proven": live_map[aid],
            "repeated_tick_ready": bool(uv.get("repeated_tick_ready")),
            "autonomous_daemon_ready": daemon_ok,
            "lessons_runtime_influence_proven": les_ok if aid == "B" else False,
            "buy_sell_log_rebuy_runtime_proven": loop_ok,
            "exact_blockers": list(blockers),
            "exact_advisories": [] if sw else ["Resolve blockers before scaling notional."],
            "exact_proof_sources": proof_sources,
            "no_cross_avenue_inheritance": True,
        }
    return {
        "truth_version": "final_avenue_readiness_authority_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "avenues": out,
        "policy": "Each avenue evaluated only from its gates + independent proofs — never from sibling avenue.",
    }


def build_final_go_live_decision_engine(
    *,
    runtime_root: Path,
    a_block: Dict[str, Any],
    final_gap_doc: Dict[str, Any],
    bslr: Dict[str, Any],
    les: Dict[str, Any],
    crt: Dict[str, Any],
) -> Dict[str, Any]:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    root = Path(runtime_root).resolve()
    sw_a, bl_a, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    sw_b, bl_b, _ = compute_avenue_switch_live_now("B", runtime_root=root)
    sw_c, bl_c, _ = compute_avenue_switch_live_now("C", runtime_root=root)

    merged = final_gap_doc.get("gaps") or []
    critical = [g for g in merged if g.get("classification") == "blocks_live_now"]
    noncrit = [g for g in merged if g.get("classification") != "blocks_live_now"]

    can_go = bool(a_block.get("can_switch_live_now")) and len(critical) == 0
    decision = "GO_LIVE_ALLOWED" if can_go else "DO_NOT_GO_LIVE"

    arts = [
        "data/control/system_execution_lock.json",
        "data/control/operator_live_confirmation.json",
        "data/control/universal_execution_loop_proof.json",
        "data/control/gate_b_final_go_live_truth.json",
        "data/control/go_no_go_decision.json",
        "data/control/execution_mirror_results.json",
        "data/control/final_remaining_gaps_before_live.json",
    ]

    return {
        "truth_version": "final_go_live_decision_engine_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "FINAL_DECISION": decision,
        "avenue_a_can_go_live_now": bool(sw_a),
        "avenue_b_can_go_live_now": bool(sw_b),
        "avenue_c_can_go_live_now": bool(sw_c),
        "buy_sell_log_rebuy_proven": bool(bslr.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN")),
        "first_20_ready_for_next_phase": False,
        "lessons_runtime_influence_proven": bool(les.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN")),
        "continuous_daemon_runtime_proven": bool(crt.get("CONTINUOUS_DAEMON_RUNTIME_PROVEN")),
        "exact_critical_blockers": sorted(
            {
                str(x)
                for x in list(a_block.get("critical_blockers") or [])
                + [g.get("title") for g in critical if g.get("title")]
            }
        ),
        "exact_noncritical_remaining_gaps": [g.get("title") for g in noncrit][:80],
        "exact_next_operator_step": (
            "Supervised smallest live trade + refresh closure bundle"
            if can_go
            else "Resolve exact_critical_blockers; refresh closure; re-read final_go_live_decision.json"
        ),
        "exact_artifacts_operator_must_read": arts,
        "logic_note": "Profitability alone never authorizes live; safety + execution truth + switch_live required.",
        "first_20_blocks_avenue_a_only_if_env": "EZRAS_FIRST_20_REQUIRED_FOR_LIVE=true (default: first-20 is advisory for Avenue A)",
    }


def patch_go_live_decision_with_first_20(payload: Dict[str, Any], f20_final: Dict[str, Any]) -> Dict[str, Any]:
    p = dict(payload)
    p["first_20_ready_for_next_phase"] = bool(f20_final.get("FIRST_20_READY_FOR_NEXT_PHASE"))
    p["first_20_safe_for_live_capital"] = bool(f20_final.get("FIRST_20_SAFE_FOR_LIVE_CAPITAL"))
    p["first_20_blocks_avenue_a_live"] = bool(f20_final.get("first_20_blocks_avenue_a_live"))
    return p


def build_lessons_runtime_final_truth(*, runtime_root: Path) -> Dict[str, Any]:
    """
    Strict: only score/block/exit/rebuy mutations count — not file existence or context read.
    """
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    eff = ad.read_json("data/control/lessons_runtime_effect.json") or {}
    rank = bool(eff.get("influenced_ranking"))
    ent = bool(eff.get("influenced_entry"))
    ex = bool(eff.get("influenced_exit"))
    reb = bool(eff.get("influenced_rebuy"))
    proven = bool(eff.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"))
    wiring = (
        "Gate B: trading_ai/shark/lesson_runtime_influence.py mutates evaluation when lessons match. "
        "Avenue A NTE order path does not call lesson_runtime_influence — lessons do not change live A decisions. "
        "Avenue C: not wired."
    )
    why_false = (
        ""
        if proven
        else "No LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN and no influenced_* true in lessons_runtime_effect.json."
    )
    return {
        "truth_version": "lessons_runtime_final_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "are_lessons_changing_avenue_a_live_decisions": False,
        "are_lessons_changing_gate_b_ranking": rank,
        "are_lessons_changing_entry": ent,
        "are_lessons_changing_exit": ex,
        "are_lessons_changing_rebuy": reb,
        "avenue_a_influence_proven": False,
        "avenue_b_influence_proven": bool(proven and (rank or ent or ex or reb)),
        "avenue_c_influence_proven": False,
        "influenced_ranking": rank,
        "influenced_entry": ent,
        "influenced_exit": ex,
        "influenced_rebuy": reb,
        "LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN": proven,
        "exact_reason_if_false": why_false,
        "exact_wiring_needed_if_false": wiring if not proven else "",
        "source_artifact": "data/control/lessons_runtime_effect.json",
        "honesty": "Stored lessons under data/learning without runtime hooks do not count as influence.",
    }


def build_runtime_runner_final_truth(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration import runtime_runner as rr

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    mode = rr.global_runner_mode()
    proven = rr.evaluate_continuous_daemon_runtime_proven(runtime_root=root)
    why = (
        ""
        if proven
        else "CONTINUOUS_DAEMON_RUNTIME_PROVEN requires runtime_runner_daemon_verification.json with both verified flags."
    )
    tick_only = mode in ("tick_only", "disabled", "paper_execution")
    return {
        "truth_version": "runtime_runner_final_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner_exists": True,
        "lock_exclusivity_implemented": True,
        "failure_stop_implemented": True,
        "heartbeat_implemented": True,
        "places_live_orders_directly": False,
        "places_live_orders_directly_note": "run_cycle never submits venue orders; live_execution mode still documents stub — venue adapters are external.",
        "tick_only_orchestration": tick_only,
        "tick_only_orchestration_note": "True when runner mode is not live_execution — orchestration-only tick paths.",
        "CONTINUOUS_DAEMON_RUNTIME_PROVEN": proven,
        "continuous_autonomous_production_ready": proven,
        "exact_reason_if_false": why,
        "EZRAS_RUNNER_MODE": mode,
        "daemon_verification_snapshot": {
            "lock_exclusivity_verified": bool(ver.get("lock_exclusivity_verified")),
            "failure_stop_verified": bool(ver.get("failure_stop_verified")),
        },
        "honesty": "Heartbeat file alone is not production proof — see daemon verification JSON.",
    }


def write_first_20_remaining_gaps_final(*, runtime_root: Path, base_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Extended first-20 gap list for operator (same gaps + metadata)."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    gaps = list(base_doc.get("gaps") or [])
    doc = {
        "truth_version": "first_20_remaining_gaps_final_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gap_count": len(gaps),
        "gaps": gaps,
        "inherits_from": "data/control/first_20_remaining_gaps.json",
    }
    ad.write_json("data/control/first_20_remaining_gaps_final.json", doc)
    ad.write_text("data/control/first_20_remaining_gaps_final.txt", json.dumps(doc, indent=2) + "\n")
    return doc


def write_avenue_a_final_section_i(
    *,
    runtime_root: Path,
    can_go_live: bool,
    a_block: Dict[str, Any],
    final_gap: Dict[str, Any],
) -> List[str]:
    """Exact filenames from user Section I."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    paths: List[str] = []
    critical = [g for g in (final_gap.get("gaps") or []) if g.get("classification") == "blocks_live_now"]
    can = bool(can_go_live) and len(critical) == 0

    if can:
        body = {
            "prerequisite_artifact_checks": [
                "data/control/system_execution_lock.json",
                "data/control/operator_live_confirmation.json",
                "data/control/gate_b_final_go_live_truth.json",
            ],
            "exact_env_flags_required": [
                "EZRAS_RUNTIME_ROOT",
                "EZRAS_OPERATOR_LIVE_CONFIRMED or operator_live_confirmation.json",
                "Venue credentials per deployment",
            ],
            "exact_command_order": [
                "Validate closure: python -c \"from trading_ai.operator_truth import write_live_switch_closure_bundle; write_live_switch_closure_bundle()\"",
                "Run venue micro validation per deployment docs",
            ],
            "inspect_immediately_after_activation": [
                "data/control/universal_execution_loop_proof.json",
                "logs/post_trade_log.md",
            ],
            "artifacts_after_first_live_trades": [
                "universal_execution_loop_proof.json refresh",
                "first_20 diagnostics if active",
            ],
            "stop_immediately_if": a_block.get("critical_blockers") or ["kill_switch", "global halt", "first_20 PAUSED_REVIEW"],
            "does_not_count_as_proof": [
                "Green JSON without venue-confirmed fills",
                "Tests only without production correlation",
            ],
        }
        ad.write_json("data/control/avenue_a_final_safe_activation_sequence.json", body)
        ad.write_text("data/control/avenue_a_final_safe_activation_sequence.txt", json.dumps(body, indent=2) + "\n")
        paths = [
            "data/control/avenue_a_final_safe_activation_sequence.json",
            "data/control/avenue_a_final_safe_activation_sequence.txt",
        ]
    else:
        crit_raw = a_block.get("critical_blockers") or []
        block_rows: List[Dict[str, Any]] = []
        for i, g in enumerate(crit_raw[:20]):
            s = str(g)
            block_rows.append(
                {
                    "blocker_id": f"crit_{i}",
                    "exact_reason": s,
                    "exact_fix": "Resolve switch_live / gate artifacts",
                    "next_command": None,
                    "operator_clearable": "operator_live_confirmation" in s,
                    "code_required": "scaffold" in s.lower(),
                }
            )
        blk = {
            "blockers": block_rows,
            "can_switch_live_now": a_block.get("can_switch_live_now"),
            "exact_reason_summary": a_block.get("exact_reason_if_false"),
        }
        ad.write_json("data/control/avenue_a_final_activation_blockers.json", blk)
        ad.write_text("data/control/avenue_a_final_activation_blockers.txt", json.dumps(blk, indent=2) + "\n")
        paths = [
            "data/control/avenue_a_final_activation_blockers.json",
            "data/control/avenue_a_final_activation_blockers.txt",
        ]
    return paths
