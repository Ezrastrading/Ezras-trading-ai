"""
Supervised live Avenue A (Gate A) — thin truth layer on top of real live_execution_validation proof.

No mock orders: records only what ``execution_proof/live_execution_validation.json`` + pipeline prove.
Does not enable daemon or autonomous trading.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter

TRUTH_VERSION = "avenue_a_supervised_live_truth_v1"
SESSION_SUMMARY_VERSION = "avenue_a_supervised_session_summary_v1"
MATERIAL_VERSION = "supervised_material_change_truth_v1"
DAEMON_ENABLE_VERSION = "daemon_enable_readiness_after_supervised_v2"

_REL_TRUTH = "data/control/avenue_a_supervised_live_truth.json"
_REL_LOG = "data/control/avenue_a_supervised_trade_log.jsonl"
_REL_SESSION = "data/control/avenue_a_supervised_session_summary.json"
_REL_MATERIAL = "data/control/supervised_material_change_truth.json"
_REL_DAEMON_READY = "data/control/daemon_enable_readiness_after_supervised.json"

_DAEMON_ACTIVE_ENV = "EZRAS_AVENUE_A_DAEMON_ACTIVE"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_clean_trades() -> int:
    raw = (os.environ.get("EZRAS_SUPERVISED_CLEAN_TRADES_FOR_PROVEN") or "2").strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 2


def is_daemon_sourced_gate_a_context() -> bool:
    """True when the live validation run is part of an Avenue A daemon cycle (not laptop-supervised first trades)."""
    return (os.environ.get(_DAEMON_ACTIVE_ENV) or "").strip().lower() in ("1", "true", "yes")


def strict_full_proof_from_disk(g: Dict[str, Any]) -> Tuple[bool, str]:
    """Same contract as gate bridge — no file-existence shortcuts."""
    from trading_ai.universal_execution.gate_b_proof_bridge import gate_b_file_proves_full_contract

    return gate_b_file_proves_full_contract(g)


def _read_gate_a_proof(runtime_root: Path) -> Optional[Dict[str, Any]]:
    p = runtime_root / "execution_proof" / "live_execution_validation.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _runtime_root_matches_proof(runtime_root: Path, g: Dict[str, Any]) -> Tuple[bool, str]:
    pr = str(g.get("runtime_root") or "").strip()
    if not pr:
        return False, "proof_missing_runtime_root_field_refuse_cross_root_inference"
    try:
        if Path(pr).resolve() != Path(runtime_root).resolve():
            return False, "proof_runtime_root_mismatch_vs_process"
    except OSError:
        return False, "proof_runtime_root_unresolvable"
    return True, "ok"


def append_supervised_trade_log_line(
    *,
    runtime_root: Path,
    record: Dict[str, Any],
) -> None:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.ensure_parent(_REL_LOG)
    line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
    with (ad.root() / _REL_LOG).open("a", encoding="utf-8") as fh:
        fh.write(line)


def load_supervised_log_records(runtime_root: Path) -> List[Dict[str, Any]]:
    p = Path(runtime_root) / _REL_LOG
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


_DEFAULT_OPERATOR_SOURCES: Tuple[str, ...] = ("supervised_operator_session",)
_RUNTIME_PROVEN_SOURCES: Tuple[str, ...] = ("supervised_operator_session", "avenue_a_daemon_cycle")


def rollup_supervised_session(
    records: List[Dict[str, Any]],
    *,
    ledger_sources: Sequence[str] = _DEFAULT_OPERATOR_SOURCES,
) -> Dict[str, Any]:
    """
    Roll up supervised trade log lines filtered by ``ledger_sources``.

    - Default ``("supervised_operator_session",)``: operator laptop session only (session summary).
    - ``("supervised_operator_session", "avenue_a_daemon_cycle")``: also counts clean Avenue A daemon
      cycles toward **supervised_live_runtime_proven** tidy streak (same ``outcome_class`` contract).
    """
    allowed = frozenset(str(s) for s in ledger_sources)
    filt: List[Dict[str, Any]] = []
    for r in records:
        if str(r.get("source") or "") not in allowed:
            continue
        filt.append(r)

    total = len(filt)
    clean = [r for r in filt if r.get("outcome_class") == "clean_full_proof"]
    failures = [r for r in filt if r.get("outcome_class") == "failed_pipeline"]
    partials = [r for r in filt if r.get("outcome_class") == "partial_or_unproven"]

    sigs: Dict[str, int] = {}
    for r in filt:
        if r.get("outcome_class") != "clean_full_proof":
            sig = str(r.get("failure_signature") or r.get("terminal_reason") or "unknown")
            sigs[sig] = sigs.get(sig, 0) + 1

    consecutive = 0
    for r in reversed(filt):
        if r.get("outcome_class") == "clean_full_proof":
            consecutive += 1
        else:
            break

    req = _required_clean_trades()
    tidy = consecutive >= req and len(partials) == 0 and total >= req

    return {
        "truth_version": SESSION_SUMMARY_VERSION,
        "generated_at": _iso(),
        "total_supervised_trades": total,
        "passes_clean_full_proof": len(clean),
        "failures": len(failures),
        "partial_failures": len(partials),
        "consecutive_clean_supervised_trades": consecutive,
        "repeated_failure_signatures": sigs,
        "required_clean_trades_for_tidy": req,
        "system_tidy_enough_for_daemon_enable_review": bool(tidy),
        "ledger_sources": list(ledger_sources),
        "honesty": (
            "Filtered by ledger_sources; default is operator_session-only. "
            "supervised_live_runtime_proven uses operator + avenue_a_daemon_cycle when computing tidy streak."
        ),
    }


def write_supervised_session_summary(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    recs = load_supervised_log_records(root)
    payload = rollup_supervised_session(recs)
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_REL_SESSION, payload)
    ad.write_text(_REL_SESSION.replace(".json", ".txt"), json.dumps(payload, indent=2, default=str) + "\n")
    try:
        from trading_ai.global_layer.trade_cycle_intelligence import refresh_trade_cycle_intelligence_bundle

        refresh_trade_cycle_intelligence_bundle(root)
    except Exception:
        pass
    return payload


def write_avenue_a_supervised_live_truth(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    g = _read_gate_a_proof(root)
    recs = load_supervised_log_records(root)
    roll = rollup_supervised_session(recs, ledger_sources=_RUNTIME_PROVEN_SOURCES)

    proof_ok, proof_why = (False, "no_live_execution_validation_json")
    if g:
        proof_ok, proof_why = strict_full_proof_from_disk(g)
    rr_ok, rr_why = (True, "")
    if g:
        rr_ok, rr_why = _runtime_root_matches_proof(root, g)

    sup_clean = roll.get("passes_clean_full_proof") or 0
    req = _required_clean_trades()
    proven = bool(
        rr_ok
        and sup_clean >= req
        and roll.get("partial_failures", 1) == 0
        and bool(roll.get("system_tidy_enough_for_daemon_enable_review"))
    )

    last_id = ""
    last_ts = ""
    for r in reversed(recs):
        if r.get("source") == "supervised_operator_session":
            last_id = str(r.get("trade_id") or "")
            last_ts = str(r.get("timestamp") or "")
            break

    exact_sources = [
        "execution_proof/live_execution_validation.json",
        _REL_LOG,
        "data/control/universal_execution_loop_proof.json",
    ]

    why_false = ""
    if not proven:
        parts = []
        if not rr_ok:
            parts.append(rr_why)
        if sup_clean < req:
            parts.append(f"need_{req}_clean_supervised_trades_have_{sup_clean}")
        if roll.get("partial_failures"):
            parts.append("partial_failures_present_in_supervised_ledger")
        if not proof_ok and g:
            parts.append(f"latest_gate_a_proof_not_full:{proof_why}")
        why_false = ";".join(parts) if parts else "supervised_runtime_not_proven"

    last_ok_meta = ad.read_json("data/control/gate_a_last_successful_live_proof_meta.json") or {}
    latest_stage = str(g.get("failure_stage") or "") if g else ""
    latest_failed_pre = bool(
        g
        and not bool(g.get("FINAL_EXECUTION_PROVEN"))
        and latest_stage == "pre_buy"
    )

    payload = {
        "truth_version": TRUTH_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "supervised_live_trade_count": int(roll.get("total_supervised_trades") or 0),
        "supervised_live_runtime_proven": proven,
        "supervised_live_last_trade_id": last_id or None,
        "supervised_live_last_updated_at": last_ts or None,
        "live_capital_used": bool(g.get("FINAL_EXECUTION_PROVEN")) if g else None,
        "real_orders_submitted": bool(g.get("execution_success")) if g else False,
        "exact_proof_sources": exact_sources,
        "exact_reason_if_false": "" if proven else why_false,
        "latest_gate_a_proof_strict_ok": proof_ok,
        "latest_gate_a_proof_strict_reason": proof_why if g else "no_file",
        "runtime_root_match": rr_ok,
        "runtime_root_mismatch_reason": "" if rr_ok else rr_why,
        "ledger_sources_for_runtime_proven": list(_RUNTIME_PROVEN_SOURCES),
        "honesty": (
            "supervised_live_runtime_proven requires strict latest Gate A proof on disk + runtime_root match + "
            "clean ledger streak (operator session and/or avenue_a_daemon_cycle clean_full_proof per ledger_sources)."
        ),
        "last_successful_full_gate_a_trade_id": last_ok_meta.get("last_successful_trade_id"),
        "last_successful_full_gate_a_recorded_at": last_ok_meta.get("last_successful_at"),
        "latest_on_disk_gate_a_snapshot": {
            "failure_stage": g.get("failure_stage") if g else None,
            "failure_code": g.get("failure_code") if g else None,
            "FINAL_EXECUTION_PROVEN": g.get("FINAL_EXECUTION_PROVEN") if g else None,
            "note": (
                "Reflects execution_proof/live_execution_validation.json at recompute time — may show a failed "
                "pre-execution attempt even when last_successful_full_gate_a_* still records an earlier full proof."
            ),
        },
        "latest_attempt_failed_pre_execution": latest_failed_pre,
        "latest_attempt_failure_code": (g.get("failure_code") if g and not g.get("FINAL_EXECUTION_PROVEN") else None),
    }
    ad.write_json(_REL_TRUTH, payload)
    ad.write_text(_REL_TRUTH.replace(".json", ".txt"), json.dumps(payload, indent=2, default=str) + "\n")
    return payload


def compute_first_20_fields_for_supervised_daemon(
    *,
    runtime_root: Path,
    f20_final: Dict[str, Any],
    supervised_live_runtime_proven: bool,
) -> Dict[str, Any]:
    """
    Separate **full first-20 program completion** from **safe to enable supervised daemon**.

    - ``first_20_ready_for_next_phase``: full diagnostic / closure readiness (strict program bar).
    - ``first_20_safe_enough_for_daemon``: conservative gate for *supervised* Avenue A daemon only;
      does not require 20 diagnostic trades if supervised Gate A chain is already proven and phase is not failed/paused.

    ``EZRAS_FIRST_20_STRICT_FOR_SUPERVISED_DAEMON=true`` restores legacy behavior (require READY_FOR_NEXT_PHASE).
    """
    from trading_ai.first_20.constants import P_PASS_DECISION, P_TRUTH, PhaseStatus

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    truth_core = ad.read_json(P_TRUTH) or {}
    pass_doc = ad.read_json(P_PASS_DECISION) or {}

    ready_next = bool(
        f20_final.get("FIRST_20_READY_FOR_NEXT_PHASE") or truth_core.get("ready_for_next_phase")
    )
    safe_live_capital = bool(f20_final.get("FIRST_20_SAFE_FOR_LIVE_CAPITAL"))

    phase = str(truth_core.get("phase_status") or "")
    require_f20_live = (os.environ.get("EZRAS_FIRST_20_REQUIRED_FOR_LIVE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    strict_daemon = (os.environ.get("EZRAS_FIRST_20_STRICT_FOR_SUPERVISED_DAEMON") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    hard_blockers: List[str] = []
    if phase == PhaseStatus.FAILED_REWORK_REQUIRED.value:
        hard_blockers.append("first_20_phase_failed_rework")
    if phase == PhaseStatus.PAUSED_REVIEW_REQUIRED.value:
        hard_blockers.append("first_20_paused_review_required")
    if require_f20_live and not bool(pass_doc.get("passed")):
        hard_blockers.append("first_20_pass_required_by_env_but_false")

    policy_reason = ""
    safe_enough = False

    if strict_daemon:
        safe_enough = bool(ready_next) and len(hard_blockers) == 0
        policy_reason = "strict_env_FIRST_20_READY_FOR_NEXT_PHASE_required" if not safe_enough else "strict_env_ok"
    elif hard_blockers:
        safe_enough = False
        policy_reason = hard_blockers[0]
    elif supervised_live_runtime_proven:
        safe_enough = True
        policy_reason = (
            "supervised_avenue_a_runtime_proven_first_20_full_program_not_required_for_supervised_daemon"
        )
    else:
        safe_enough = bool(ready_next or safe_live_capital)
        policy_reason = (
            "first_20_ready_or_safe_for_live_capital"
            if safe_enough
            else "no_supervised_proof_and_first_20_closure_not_ready"
        )

    return {
        "first_20_ready_for_next_phase": ready_next,
        "first_20_safe_for_live_capital_from_closure": safe_live_capital,
        "first_20_safe_enough_for_daemon": safe_enough,
        "first_20_policy_reason": policy_reason,
        "first_20_hard_blockers": hard_blockers,
        "first_20_phase_status": phase,
        "first_20_pass_decision_passed": bool(pass_doc.get("passed")),
        "first_20_strict_supervised_daemon_env": strict_daemon,
        "honesty": (
            "supervised daemon uses first_20_safe_enough_for_daemon; autonomous/full-program gates stay elsewhere. "
            "Set EZRAS_FIRST_20_STRICT_FOR_SUPERVISED_DAEMON=1 to require full FIRST_20_READY_FOR_NEXT_PHASE."
        ),
    }


def build_buy_sell_log_rebuy_flags(*, runtime_root: Path) -> Tuple[bool, bool]:
    from trading_ai.orchestration.final_pre_live_writers import build_buy_sell_log_rebuy_certification

    doc = build_buy_sell_log_rebuy_certification(runtime_root=runtime_root)
    bslr = bool(doc.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN"))
    loop = LocalStorageAdapter(runtime_root=runtime_root).read_json("data/control/universal_execution_loop_proof.json") or {}
    ulp = bool(loop.get("final_execution_proven"))
    return bslr, ulp


def build_daemon_enable_readiness_after_supervised(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration.daemon_live_authority import build_daemon_live_switch_authority
    from trading_ai.safety.failsafe_guard import load_kill_switch

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)

    sup_truth = ad.read_json(_REL_TRUTH) or {}
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    gng = ad.read_json("data/control/go_no_go_decision.json") or {}
    mirror = ad.read_json("data/control/execution_mirror_results.json")
    op = ad.read_json("data/control/operator_live_confirmation.json") or {}
    gh = ad.read_json("data/control/gate_b_global_halt_truth.json") or {}
    f20 = ad.read_json("data/control/first_20_final_truth.json") or {}

    bslr, ulp_loop = build_buy_sell_log_rebuy_flags(runtime_root=root)
    supervised_proven = bool(sup_truth.get("supervised_live_runtime_proven"))

    f20_policy = compute_first_20_fields_for_supervised_daemon(
        runtime_root=root,
        f20_final=f20,
        supervised_live_runtime_proven=supervised_proven,
    )
    f20_safe = bool(f20_policy.get("first_20_safe_enough_for_daemon"))

    try:
        auth = build_daemon_live_switch_authority(runtime_root=root)
    except Exception as exc:
        auth = {"error": str(exc)}

    sup_live_ok = bool(isinstance(auth, dict) and auth.get("avenue_a_can_run_supervised_live_now"))
    op_ok = bool(op.get("confirmed") is True) or (os.environ.get("EZRAS_OPERATOR_LIVE_CONFIRMED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    mirror_ok = mirror is None or bool(mirror.get("ok") is not False)
    go_ok = bool(gng.get("ready_for_first_5_trades") is not False)
    halt = bool(gh.get("global_halt_is_currently_authoritative"))
    ks = load_kill_switch(runtime_root=root)

    blockers: List[str] = []
    if ks:
        blockers.append("system_kill_switch_active")
    if halt:
        blockers.append("authoritative_global_halt")
    if not op_ok:
        blockers.append("operator_live_confirmation_missing")
    if not mirror_ok:
        blockers.append("execution_mirror_not_ok")
    if not go_ok:
        blockers.append("go_no_go_not_ready_for_first_5_trades")
    if not supervised_proven:
        blockers.append("supervised_live_runtime_not_proven")
    if not bslr:
        blockers.append("buy_sell_log_rebuy_runtime_proven_false")
    if not ulp_loop:
        blockers.append("universal_loop_proof_final_execution_not_proven")
    if not f20_safe:
        blockers.append(
            "first_20_not_safe_enough_for_supervised_daemon:"
            + str(f20_policy.get("first_20_policy_reason") or "see_first_20_fields_in_payload")
        )
    if isinstance(auth, dict) and not sup_live_ok:
        blockers.append("avenue_a_can_run_supervised_live_now_false")
        blockers.extend([str(x) for x in (auth.get("exact_blockers_supervised") or [])[:16]])

    can_enable = len(blockers) == 0 and sup_live_ok

    next_cmd = ""
    if not can_enable:
        next_cmd = (
            "Fix exact_blockers; run supervised Gate A trades while at laptop; "
            "then: python -m trading_ai.deployment write-supervised-live-truth && "
            "python -m trading_ai.deployment write-daemon-enable-readiness-after-supervised"
        )
    enable_seq = []
    if can_enable:
        enable_seq = [
            "Verify avenue_a_can_enable_daemon_now true in daemon_enable_readiness_after_supervised.json",
            "Export EZRAS_AVENUE_A_DAEMON_MODE=supervised_live (still attended)",
            "Confirm autonomous remains OFF until separate policy review",
            "python -m trading_ai.deployment avenue-a-daemon-start (only when operator ready)",
        ]

    payload = {
        "truth_version": DAEMON_ENABLE_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "avenue_a_can_enable_daemon_now": bool(can_enable),
        "avenue_a_can_switch_daemon_live_now": bool(can_enable),
        "supervised_live_runtime_proven": supervised_proven,
        "buy_sell_log_rebuy_runtime_proven": bslr,
        "universal_loop_proof_proven": ulp_loop,
        "first_20_ready_for_next_phase": bool(f20_policy.get("first_20_ready_for_next_phase")),
        "first_20_safe_enough_for_daemon": f20_safe,
        "first_20_daemon_gate_policy": f20_policy,
        "operator_confirmation_present": op_ok,
        "go_no_go_present": bool(gng),
        "execution_mirror_ok": mirror_ok,
        "no_authoritative_halt": not halt,
        "exact_blockers": blockers,
        "exact_next_command_if_false": next_cmd if not can_enable else "",
        "exact_enable_sequence_if_true": enable_seq,
        "embedded_daemon_live_switch_authority_error": auth.get("error") if isinstance(auth, dict) else None,
        "honesty": "Does not arm autonomous daemon; Gate A switch + supervised proof + loop cert must align.",
    }
    ad.write_json(_REL_DAEMON_READY, payload)
    ad.write_text(_REL_DAEMON_READY.replace(".json", ".txt"), json.dumps(payload, indent=2, default=str) + "\n")
    return payload


def write_supervised_material_change_truth(
    *,
    runtime_root: Path,
    reason: str,
    refresh_results: Dict[str, Any],
) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    prev = ad.read_json(_REL_MATERIAL) or {}
    chain = list(prev.get("refresh_chain") or [])
    chain.append(
        {
            "timestamp": _iso(),
            "reason": reason,
            "artifacts_touched": refresh_results.get("artifacts_refreshed") or [],
            "skipped_fresh": refresh_results.get("artifacts_skipped_as_fresh") or [],
            "closure": refresh_results.get("live_switch_closure"),
        }
    )
    payload = {
        "truth_version": MATERIAL_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "last_reason": reason,
        "refresh_chain": chain[-50:],
        "last_refresh_engine_summary": refresh_results,
    }
    ad.write_json(_REL_MATERIAL, payload)
    ad.write_text(_REL_MATERIAL.replace(".json", ".txt"), json.dumps(payload, indent=2, default=str) + "\n")
    return payload


def _run_closure_and_refresh(*, runtime_root: Path, reason: str) -> Dict[str, Any]:
    from trading_ai.orchestration.armed_but_off_authority import write_ceo_session_runtime_truth
    from trading_ai.orchestration.avenue_a_daemon_artifacts import write_ceo_session_truth
    from trading_ai.orchestration.final_pre_live_writers import build_buy_sell_log_rebuy_certification
    from trading_ai.reports.runtime_artifact_refresh_manager import run_refresh_runtime_artifacts
    from trading_ai.universal_execution.runtime_truth_material_change import refresh_runtime_truth_after_material_change

    root = Path(runtime_root).resolve()
    out: Dict[str, Any] = {}

    rr = refresh_runtime_truth_after_material_change(
        reason=reason,
        runtime_root=root,
        force=True,
        include_advisory=True,
    )
    out["material_refresh"] = rr

    try:
        from trading_ai.orchestration.final_pre_live_writers import write_first_20_merged_final_truth_files

        out["first_20_merged_final_truth"] = write_first_20_merged_final_truth_files(runtime_root=root)
    except Exception as exc:
        out["first_20_merged_final_truth"] = {"error": str(exc)}

    bslr = build_buy_sell_log_rebuy_certification(runtime_root=root)
    out["buy_sell_log_rebuy_certification"] = {"BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": bslr.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN")}

    try:
        out["ceo_session_truth"] = write_ceo_session_truth(runtime_root=root)
    except Exception as exc:
        out["ceo_session_truth"] = {"error": str(exc)}
    try:
        out["ceo_session_runtime_truth"] = write_ceo_session_runtime_truth(runtime_root=root)
    except Exception as exc:
        out["ceo_session_runtime_truth"] = {"error": str(exc)}

    try:
        art = run_refresh_runtime_artifacts(runtime_root=root, force=True, include_advisory=True, print_final_switch_truth=False)
        out["runtime_artifact_refresh_registry"] = art
    except Exception as exc:
        out["runtime_artifact_refresh_registry"] = {"error": str(exc)}

    write_supervised_material_change_truth(runtime_root=root, reason=reason, refresh_results=out.get("material_refresh") or {})
    write_avenue_a_supervised_live_truth(runtime_root=root)
    write_supervised_session_summary(runtime_root=root)
    build_daemon_enable_readiness_after_supervised(runtime_root=root)

    return out


def on_gate_a_live_validation_proof_written(*, runtime_root: Path, pipeline_out: Dict[str, Any]) -> Dict[str, Any]:
    """
    Called immediately after ``live_execution_validation.json`` is written for Gate A.
    Uses disk proof as source of truth (not only in-memory dict).
    """
    import logging

    logger = logging.getLogger(__name__)
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)

    g = _read_gate_a_proof(root)
    if not g:
        return {"ok": False, "reason": "no_proof_file"}

    rr_ok, rr_why = _runtime_root_matches_proof(root, g)
    strict_ok, strict_why = strict_full_proof_from_disk(g)

    daemon = is_daemon_sourced_gate_a_context()
    source = "avenue_a_daemon_cycle" if daemon else "supervised_operator_session"

    outcome = "partial_or_unproven"
    if strict_ok and rr_ok:
        outcome = "clean_full_proof"
    elif g.get("partial_failure_codes"):
        outcome = "partial_or_unproven"
    else:
        outcome = "failed_pipeline"

    trade_id = str(g.get("trade_id") or pipeline_out.get("trade_id") or "")
    product_id = str(g.get("product_id") or pipeline_out.get("product_id") or "")

    ready_for_rebuy: Any = None
    refresh_out: Dict[str, Any] = {}
    if strict_ok and rr_ok:
        from trading_ai.universal_execution.gate_b_proof_bridge import try_emit_universal_loop_proof_from_gate_a_file

        emit = try_emit_universal_loop_proof_from_gate_a_file(runtime_root=root, overwrite_if_unproven=True, force_refresh=True)
        refresh_out["universal_loop_emit"] = emit

        lp = LocalStorageAdapter(runtime_root=root).read_json("data/control/universal_execution_loop_proof.json") or {}
        ready_for_rebuy = lp.get("ready_for_rebuy")

        refresh_out.update(
            _run_closure_and_refresh(
                runtime_root=root,
                reason=f"gate_a_supervised_live_trade:{trade_id}",
            )
        )
    else:
        write_avenue_a_supervised_live_truth(runtime_root=root)
        write_supervised_session_summary(runtime_root=root)
        build_daemon_enable_readiness_after_supervised(runtime_root=root)
        logger.warning("supervised_avenue_a: proof not strict clean — %s / %s", strict_why, rr_why)

    record = {
        "trade_id": trade_id,
        "buy_order_id": (g.get("buy_leg_diagnostics") or {}).get("order_id") if isinstance(g.get("buy_leg_diagnostics"), dict) else None,
        "sell_order_id": (g.get("sell_leg_diagnostics") or {}).get("order_id") if isinstance(g.get("sell_leg_diagnostics"), dict) else None,
        "product_id": product_id,
        "gate_id": "gate_a",
        "execution_profile": "gate_a",
        "buy_fill_confirmed": g.get("buy_fill_confirmed"),
        "sell_fill_confirmed": g.get("sell_fill_confirmed"),
        "pnl": g.get("realized_pnl"),
        "local_write_ok": bool(g.get("databank_written")),
        "remote_sync_ok": bool(g.get("supabase_synced")),
        "governance_ok": bool(g.get("governance_logged")),
        "review_ok": bool(g.get("packet_updated")),
        "final_execution_proven": bool(g.get("FINAL_EXECUTION_PROVEN")),
        "ready_for_rebuy": ready_for_rebuy,
        "timestamp": _iso(),
        "source": source,
        "outcome_class": outcome,
        "failure_signature": strict_why if not strict_ok else "",
        "terminal_reason": rr_why if not rr_ok else "",
    }

    append_supervised_trade_log_line(runtime_root=root, record=record)

    return {"ok": True, "record": record, "refresh": refresh_out}


def write_all_supervised_artifacts_cli(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Operator CLI: recompute supervised truth from existing disk proofs + ledger (no orders)."""
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    out = {
        "avenue_a_supervised_live_truth": write_avenue_a_supervised_live_truth(runtime_root=root),
        "avenue_a_supervised_session_summary": write_supervised_session_summary(runtime_root=root),
        "daemon_enable_readiness_after_supervised": build_daemon_enable_readiness_after_supervised(runtime_root=root),
    }
    return out


def refresh_supervised_daemon_truth_chain(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Canonical operator refresh: stamp **current shell** daemon live authority + env fingerprint, then
    recompute supervised truth, session summary, and daemon-enable readiness (idempotent, no orders).

    Run after changing env exports or when ``daemon_runtime_consistency_truth`` reports fingerprint mismatch.
    """
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    from trading_ai.orchestration.daemon_live_authority import write_all_daemon_live_artifacts

    daemon_bundle = write_all_daemon_live_artifacts(runtime_root=root)
    supervised_bundle = write_all_supervised_artifacts_cli(runtime_root=root)
    return {
        "truth_version": "refresh_supervised_daemon_truth_chain_v1",
        "runtime_root": str(root),
        "generated_at": _iso(),
        "daemon_live_refresh": daemon_bundle,
        "supervised_refresh": supervised_bundle,
        "honesty": (
            "Re-stamps daemon_live_switch_authority.json for this process env, then rebuilds supervised artifacts. "
            "Does not delete runtime_runner_last_failure.json or trade logs."
        ),
    }
