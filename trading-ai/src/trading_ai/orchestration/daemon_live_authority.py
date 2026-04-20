"""
Single daemon-grade decision surface: runtime/env consistency, raw vs authoritative halt, supervised vs autonomous.

Does not weaken honesty — mismatches and ambiguous halt always block autonomous; stale raw halt may allow supervised only when gate_b_global_halt_truth says so.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_paths import (
    ezras_runtime_root_fingerprint_component,
    resolve_ezras_runtime_root_for_daemon_authority,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter

# Env vars that must match between artifact generation and daemon process (values hashed, not secrets).
DAEMON_ENV_FINGERPRINT_KEYS: Tuple[str, ...] = (
    "EZRAS_RUNTIME_ROOT",
    "COINBASE_EXECUTION_ENABLED",
    "COINBASE_ENABLED",
    "NTE_EXECUTION_MODE",
    "NTE_LIVE_TRADING_ENABLED",
    "EZRAS_DRY_RUN",
    "GATE_B_LIVE_EXECUTION_ENABLED",
    "GATE_B_LIVE_MICRO_VALIDATION_CONFIRM",
    "EZRAS_OPERATOR_LIVE_CONFIRMED",
    "EZRAS_REQUIRE_OPERATOR_CONFIRMATION",
    "EZRAS_RUNNER_MODE",
    "EZRAS_AVENUE_A_DAEMON_MODE",
    "EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED",
    "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM",
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_env_fingerprint_inputs() -> Dict[str, str]:
    """Non-secret env snapshot hashed into :func:`compute_env_fingerprint` (``EZRAS_RUNTIME_ROOT`` is canonicalized)."""
    parts: Dict[str, str] = {}
    for k in DAEMON_ENV_FINGERPRINT_KEYS:
        if k == "EZRAS_RUNTIME_ROOT":
            parts[k] = ezras_runtime_root_fingerprint_component()
        else:
            parts[k] = (os.environ.get(k) or "").strip()
    return parts


def compute_env_fingerprint() -> str:
    parts = compute_env_fingerprint_inputs()
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:56]


def _read_gate_b_halt(ad: LocalStorageAdapter) -> Dict[str, Any]:
    raw = ad.read_json("data/control/gate_b_global_halt_truth.json") or {}
    return raw if isinstance(raw, dict) else {}


def _read_om_global(ad: LocalStorageAdapter) -> Dict[str, Any]:
    return ad.read_json("data/control/operating_mode_state.json") or {}


def _build_gate_b_final_go_live_truth(*, runtime_root: Path) -> Dict[str, Any]:
    """Indirection so tests can patch without eager-import side effects."""
    from trading_ai.reports.gate_b_final_go_live_truth import build_gate_b_final_go_live_truth

    return build_gate_b_final_go_live_truth(runtime_root=runtime_root)


def build_daemon_runtime_consistency_truth(
    *,
    runtime_root: Path,
    stored_authority: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compare current process env + resolved runtime root to last written daemon_live_switch_authority snapshot.
    If no snapshot, record honest 'no_prior_snapshot' — first run after write will match self.
    """
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    current_root = str(root)
    resolved_from_env = ezras_runtime_root_fingerprint_component()

    fp_now = compute_env_fingerprint()
    snap = stored_authority
    if snap is None:
        ad = LocalStorageAdapter(runtime_root=root)
        snap = ad.read_json("data/control/daemon_live_switch_authority.json") or {}

    stored_root = str(snap.get("authoritative_runtime_root") or "").strip()
    stored_fp = str(snap.get("authoritative_env_fingerprint") or "").strip()

    root_match = bool(stored_root) and stored_root == current_root
    if resolved_from_env and resolved_from_env != current_root:
        root_match = False

    env_match = bool(stored_fp) and stored_fp == fp_now
    mismatched: List[str] = []
    drift_keys: List[str] = []
    if stored_root and stored_root != current_root:
        mismatched.append("runtime_root")
    if resolved_from_env and resolved_from_env != current_root:
        mismatched.append("EZRAS_RUNTIME_ROOT_process_vs_cwd")
    if stored_fp and stored_fp != fp_now:
        mismatched.append("env_fingerprint")
        prev_in = snap.get("fingerprint_inputs_canonical_snapshot")
        if isinstance(prev_in, dict):
            now_in = compute_env_fingerprint_inputs()
            for k in DAEMON_ENV_FINGERPRINT_KEYS:
                if prev_in.get(k) != now_in.get(k):
                    drift_keys.append(k)

    consistent = True
    reason = "consistent_with_authoritative_snapshot"
    if not snap:
        consistent = False
        reason = "no_daemon_live_switch_authority_snapshot_run_closure_writer_first"
    elif not root_match or not env_match:
        consistent = False
        reason = "runtime_root_or_env_fingerprint_mismatch_vs_daemon_live_switch_authority"

    payload = {
        "truth_version": "daemon_runtime_consistency_truth_v1",
        "generated_at": _iso(),
        "current_runtime_root": current_root,
        "env_EZRAS_RUNTIME_ROOT_resolved": resolved_from_env or None,
        "stored_authoritative_runtime_root": stored_root or None,
        "current_env_fingerprint": fp_now,
        "stored_authoritative_env_fingerprint": stored_fp or None,
        "runtime_root_match": root_match,
        "env_fingerprint_match": env_match,
        "env_fingerprint_drift_keys": drift_keys,
        "consistent_with_authoritative_artifacts": consistent,
        "mismatched_keys_or_surfaces": mismatched,
        "mismatched_keys": mismatched,
        "exact_do_not_run_reason_if_inconsistent": "" if consistent else reason,
        "honesty": "Fingerprint is env-key snapshot only — not secret values. Mismatch means process differs from closure that stamped authority.",
    }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/daemon_runtime_consistency_truth.json", payload)
    ad.write_text("data/control/daemon_runtime_consistency_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_daemon_last_gate_failure(
    *,
    runtime_root: Path,
    category: str,
    detail: str,
    blockers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Last daemon gate failure — exact category for operators (Section E/F)."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    payload = {
        "truth_version": "daemon_last_gate_failure_v1",
        "generated_at": _iso(),
        "category": category,
        "detail": detail,
        "blockers": list(blockers or []),
        "honesty": "Written when a gate refuses live or tick advance; does not assert orders were attempted.",
    }
    ad.write_json("data/control/daemon_last_gate_failure.json", payload)
    ad.write_text("data/control/daemon_last_gate_failure.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def build_daemon_live_switch_authority(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Single authority JSON for daemon + closure; stamped with runtime root + env fingerprint."""
    from trading_ai.orchestration.avenue_a_daemon_policy import (
        avenue_a_autonomous_runtime_proven,
        avenue_a_supervised_inputs_ok,
    )
    from trading_ai.orchestration.runtime_runner import evaluate_continuous_daemon_runtime_proven
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    ad = LocalStorageAdapter(runtime_root=root)

    # Ensure halt truth exists for authoritative fields
    try:
        from trading_ai.reports.gate_b_global_halt_truth import write_gate_b_global_halt_truth_artifacts

        write_gate_b_global_halt_truth_artifacts(runtime_root=root)
    except Exception:
        pass

    gh = _read_gate_b_halt(ad)
    om = _read_om_global(ad)
    raw_mode = str(om.get("mode") or om.get("operating_mode") or "unknown")

    primary = str(gh.get("global_halt_primary_classification") or "UNKNOWN")
    is_stale = bool(gh.get("global_halt_is_stale"))
    switch_auth = str(gh.get("gate_b_switch_live_authority") or "")

    gb_final = _build_gate_b_final_go_live_truth(runtime_root=root)
    gb_can = bool(gb_final.get("gate_b_can_be_switched_live_now"))

    sw_a, bl_a, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    sw_b, bl_b, _ = compute_avenue_switch_live_now("B", runtime_root=root)

    sup_ok, sup_why = avenue_a_supervised_inputs_ok(runtime_root=root, require_daemon_truth=False)
    aut_ok, aut_why = avenue_a_autonomous_runtime_proven(runtime_root=root)

    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    bslr = bool(loop.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") or loop.get("final_execution_proven"))
    daemon_ver = evaluate_continuous_daemon_runtime_proven(runtime_root=root)

    gh_truth_ok = bool(gh.get("truth_version"))
    halt_authoritative = bool(gh.get("global_halt_is_currently_authoritative"))
    gov_block = bool(gh.get("governance_review_currently_blocking"))

    # Raw vs authoritative: daemon uses global_halt_is_currently_authoritative from gate_b_global_halt_truth (not raw JSON alone).
    authoritative_global_halt_blocks_supervised = bool(gh_truth_ok and halt_authoritative)
    # Authoritative-only: stale-only or ambiguous primary are separate atomic blockers (not folded into "authoritative").
    authoritative_global_halt_blocks_autonomous = bool(gh_truth_ok and (halt_authoritative or gov_block))

    stale_global_halt_allowed_for_supervised = bool(gh_truth_ok and is_stale and not halt_authoritative)
    stale_global_halt_allowed_for_autonomous = False

    # Gate B daemon-safe: gb_can alone is insufficient — also require switch B clear and halt not blocking tier.
    gate_b_sup = bool(
        gh_truth_ok
        and gb_can
        and sw_b
        and len(bl_b) == 0
        and not authoritative_global_halt_blocks_supervised
    )
    gate_b_aut = bool(
        gate_b_sup
        and daemon_ver
        and bslr
        and not authoritative_global_halt_blocks_autonomous
        and not gov_block
        and not is_stale
        and primary
        not in (
            "UNKNOWN",
            "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL",
            "CONTAMINATED_SCOPE_MERGE",
        )
    )

    why_gb_not_autonomous = ""
    if gate_b_sup and not gate_b_aut:
        why_gb_not_autonomous = (
            "Gate B supervised path may clear while autonomous remains blocked: requires non-stale halt clarity, "
            "daemon verification, buy/sell/rebuy proof, and no governance/ambiguous primary classification."
        )

    # Avenue A: supervised when switch + supervised gate ok + halt not authoritative (stale => non-authoritative => allowed if switch ok).
    avenue_a_sup = bool(
        gh_truth_ok
        and sw_a
        and sup_ok
        and len(bl_a) == 0
        and not authoritative_global_halt_blocks_supervised
    )
    if not gh_truth_ok:
        avenue_a_sup = False

    avenue_a_aut = bool(
        avenue_a_sup
        and aut_ok
        and daemon_ver
        and bslr
        and not authoritative_global_halt_blocks_autonomous
        and not gov_block
        and not is_stale
        and primary
        not in (
            "UNKNOWN",
            "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL",
            "CONTAMINATED_SCOPE_MERGE",
        )
    )

    fp = compute_env_fingerprint()
    fingerprint_inputs_canonical_snapshot = compute_env_fingerprint_inputs()
    allowed_despite_raw = bool(raw_mode.lower() == "halted" and not halt_authoritative and primary == "STALE_PERSISTED_STATE")

    blockers_sup: List[str] = []
    blockers_aut: List[str] = []
    if not sw_a:
        blockers_sup.extend(bl_a)
    if not sup_ok:
        blockers_sup.append(f"supervised:{sup_why}")
    if authoritative_global_halt_blocks_supervised:
        blockers_sup.append("authoritative_global_halt_blocks_supervised")
    if not gh_truth_ok:
        blockers_sup.append("gate_b_global_halt_truth_missing_or_invalid")
    if not fp:
        blockers_sup.append("env_fingerprint_empty")

    blockers_aut.extend(blockers_sup)
    if not aut_ok:
        blockers_aut.append(f"autonomous:{aut_why}")
    if not daemon_ver:
        blockers_aut.append("daemon_verification_incomplete")
    if not bslr:
        blockers_aut.append("buy_sell_log_rebuy_not_runtime_proven")
    if is_stale:
        blockers_aut.append("stale_global_halt_classification_autonomous_forbidden")
    if primary == "UNKNOWN":
        blockers_aut.append("global_halt_primary_classification_ambiguous_or_unknown_blocks_autonomous")
    if primary == "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL":
        blockers_aut.append("global_halt_primary_classification_governance_review_required_blocks_autonomous")
    if primary == "CONTAMINATED_SCOPE_MERGE":
        blockers_aut.append("global_halt_primary_classification_contaminated_scope_merge_blocks_autonomous")
    if authoritative_global_halt_blocks_autonomous:
        blockers_aut.append("authoritative_global_halt_blocks_autonomous")
    if gov_block:
        blockers_aut.append("governance_review_currently_blocking")

    active_halt_blockers: List[str] = []
    if halt_authoritative:
        active_halt_blockers.append("authoritative_global_halt_active")
    if gov_block:
        active_halt_blockers.append("governance_review_currently_blocking")
    if is_stale:
        active_halt_blockers.append("stale_global_halt_classification_autonomous_forbidden")
    if primary == "UNKNOWN":
        active_halt_blockers.append("global_halt_primary_classification_ambiguous_or_unknown_blocks_autonomous")
    if primary == "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL":
        active_halt_blockers.append("global_halt_primary_classification_governance_review_required_blocks_autonomous")
    if primary == "CONTAMINATED_SCOPE_MERGE":
        active_halt_blockers.append("global_halt_primary_classification_contaminated_scope_merge_blocks_autonomous")

    historical_halt_notes: List[str] = []
    if is_stale and gh.get("honesty"):
        historical_halt_notes.append(f"stale_persisted_note:{str(gh.get('honesty'))[:240]}")

    can_clear_halt_now = bool(
        gh_truth_ok
        and not halt_authoritative
        and not gov_block
        and not is_stale
        and primary not in ("UNKNOWN", "CONTAMINATED_SCOPE_MERGE", "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL")
    )
    halt_decision_reason = (
        "authoritative_or_governance_blocks_autonomous"
        if (halt_authoritative or gov_block)
        else (
            "stale_classification_forbids_autonomous"
            if is_stale
            else (
                "primary_classification_ambiguous"
                if primary == "UNKNOWN"
                else "halt_chain_consistent_for_autonomous_evaluation"
            )
        )
    )

    do_not = ""
    if not avenue_a_sup:
        do_not = "; ".join(blockers_sup[:12]) or "supervised_not_clear"
    elif not avenue_a_aut:
        do_not = "supervised_may_be_ok_but_autonomous_blocked:" + "; ".join(blockers_aut[:12])

    payload: Dict[str, Any] = {
        "truth_version": "daemon_live_switch_authority_v1",
        "authoritative_truth_generated_at": _iso(),
        "authoritative_runtime_root": str(root),
        "authoritative_env_fingerprint": fp,
        "fingerprint_inputs_canonical_snapshot": fingerprint_inputs_canonical_snapshot,
        "avenue_a_can_run_supervised_live_now": avenue_a_sup,
        "avenue_a_can_run_autonomous_live_now": avenue_a_aut,
        "gate_b_can_run_supervised_live_now": gate_b_sup,
        "gate_b_can_run_autonomous_live_now": gate_b_aut,
        "raw_global_operating_mode": raw_mode,
        "raw_global_halt_state": raw_mode,
        "raw_global_halt_file_path": str(root / "data" / "control" / "operating_mode_state.json"),
        "authoritative_global_halt_classification": primary,
        "authoritative_global_halt_interpretation": {
            "primary_classification": primary,
            "global_halt_is_currently_authoritative": halt_authoritative,
            "global_halt_is_stale_persisted": is_stale,
            "gate_b_switch_live_authority": switch_auth,
        },
        "stale_persisted_state": is_stale,
        "authoritative_global_halt_blocks_supervised": authoritative_global_halt_blocks_supervised,
        "authoritative_global_halt_blocks_autonomous": authoritative_global_halt_blocks_autonomous,
        "global_halt_daemon_section_c": {
            "raw_global_operating_mode": raw_mode,
            "raw_global_halt_file_path": str(root / "data" / "control" / "operating_mode_state.json"),
            "authoritative_global_halt_classification": primary,
            "authoritative_global_halt_blocks_supervised": authoritative_global_halt_blocks_supervised,
            "authoritative_global_halt_blocks_autonomous": authoritative_global_halt_blocks_autonomous,
            "stale_persisted_state": is_stale,
            "current_global_risk_real": bool(primary == "REAL_CURRENT_GLOBAL_RISK"),
            "contaminated_scope_merge": bool(primary == "CONTAMINATED_SCOPE_MERGE"),
            "governance_review_required": gov_block,
            "operator_ack_present": bool(gh.get("operator_governance_ack_present")),
            "exact_reason_global_halt_does_or_does_not_block": gh.get("exact_do_not_go_live_reason_if_false") or "",
        },
        "gate_b_halt_interpretation": {
            "gate_b_can_be_switched_live_now_from_final_truth": gb_can,
            "allowed_despite_raw_global_halt_persisted": allowed_despite_raw,
            "allowed_despite_raw_global_halt": allowed_despite_raw,
            "why_safe_anyway": gh.get("honesty", "") if allowed_despite_raw else "",
            "why_safe_anyway_if_allowed": gh.get("honesty", "") if allowed_despite_raw else "",
            "why_not_autonomous_if_still_too_ambiguous": why_gb_not_autonomous,
        },
        "stale_global_halt_allowed_for_supervised": stale_global_halt_allowed_for_supervised,
        "stale_global_halt_allowed_for_autonomous": stale_global_halt_allowed_for_autonomous,
        "current_global_risk_real": bool(primary == "REAL_CURRENT_GLOBAL_RISK"),
        "contaminated_scope_merge": bool(primary == "CONTAMINATED_SCOPE_MERGE"),
        "governance_review_required": gov_block,
        "operator_ack_present": bool(gh.get("operator_governance_ack_present")),
        "exact_reason_global_halt_does_or_does_not_block": gh.get("exact_do_not_go_live_reason_if_false") or "",
        "exact_blockers_supervised": sorted(set(blockers_sup)),
        "exact_blockers_autonomous": sorted(set(blockers_aut)),
        "autonomous_halt_audit": {
            "active_halt_blockers": sorted(set(active_halt_blockers)),
            "historical_halt_notes": historical_halt_notes,
            "halt_authority_sources": ["data/control/gate_b_global_halt_truth.json", "data/control/operating_mode_state.json"],
            "halt_decision_reason": halt_decision_reason,
            "stale_halt_evidence_detected": is_stale,
            "can_clear_halt_now": can_clear_halt_now,
        },
        "exact_do_not_run_reason_if_false": do_not,
        "gate_b_daemon_safe_rule": (
            "Gate B live is not sufficient alone: must pass daemon_runtime_consistency_truth "
            "and this artifact's halt interpretation; gb_can true in isolation does not authorize daemon live."
        ),
        "honesty": "Autonomous requires non-stale halt clarity, daemon verification, and buy/sell/rebuy proof — never implied by code.",
    }
    ad.write_json("data/control/daemon_live_switch_authority.json", payload)
    ad.write_text("data/control/daemon_live_switch_authority.txt", json.dumps(payload, indent=2) + "\n")

    # Consistency truth after snapshot exists
    build_daemon_runtime_consistency_truth(runtime_root=root, stored_authority=payload)

    return payload


def write_daemon_mode_and_start_artifacts(*, runtime_root: Path, authority: Dict[str, Any]) -> Dict[str, Any]:
    """Section F — mode truth, blockers, sequence, last gate check."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    cons = ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}
    consistent = bool(cons.get("consistent_with_authoritative_artifacts"))

    sup = bool(authority.get("avenue_a_can_run_supervised_live_now"))
    aut = bool(authority.get("avenue_a_can_run_autonomous_live_now"))
    mode_truth = {
        "truth_version": "daemon_mode_truth_v2",
        "generated_at": _iso(),
        "daemon_process_healthy": True,
        "daemon_process_may_run_ticks": True,
        "daemon_allowed_to_run_ticks": True,
        "daemon_allowed_supervised_live": sup,
        "daemon_allowed_autonomous_live": aut,
        "runtime_env_consistent": consistent,
        "daemon_actually_placed_orders": None,
        "daemon_only_refreshed_truth": True,
        "daemon_blocked_before_live_step": (not consistent) or (not sup and not aut),
        "honesty": (
            "This file is refreshed with authority computation — not order execution. "
            "daemon_actually_placed_orders is only true when an execution layer reports fills."
        ),
    }
    ad.write_json("data/control/daemon_mode_truth.json", mode_truth)
    ad.write_text("data/control/daemon_mode_truth.txt", json.dumps(mode_truth, indent=2) + "\n")

    blockers = {
        "supervised": authority.get("exact_blockers_supervised") or [],
        "autonomous": authority.get("exact_blockers_autonomous") or [],
        "consistency": cons.get("exact_do_not_run_reason_if_inconsistent") or "",
    }
    ad.write_json("data/control/daemon_start_blockers.json", blockers)
    ad.write_text("data/control/daemon_start_blockers.txt", json.dumps(blockers, indent=2) + "\n")

    seq = {
        "steps": [
            "1. EZRAS_RUNTIME_ROOT matches data/control/daemon_live_switch_authority.json authoritative_runtime_root",
            "2. Env fingerprint matches (see daemon_runtime_consistency_truth.json)",
            "3. Read daemon_live_switch_authority.json for supervised vs autonomous",
            "4. For live: operator confirmation + avenue switch per switch_live",
            "5. Run closure: write_live_switch_closure_bundle to refresh stamped authority",
        ],
        "generated_at": _iso(),
    }
    ad.write_json("data/control/daemon_start_sequence.json", seq)
    ad.write_text("data/control/daemon_start_sequence.txt", json.dumps(seq, indent=2) + "\n")

    gate_check = {
        "ts": _iso(),
        "consistent": consistent,
        "avenue_a_supervised": authority.get("avenue_a_can_run_supervised_live_now"),
        "avenue_a_autonomous": authority.get("avenue_a_can_run_autonomous_live_now"),
    }
    ad.write_json("data/control/daemon_last_gate_check.json", gate_check)

    live_dec = {
        "ts": _iso(),
        "supervised_live_ok": bool(authority.get("avenue_a_can_run_supervised_live_now")),
        "autonomous_live_ok": bool(authority.get("avenue_a_can_run_autonomous_live_now")),
        "artifact_trust_path": "data/control/daemon_live_switch_authority.json",
    }
    ad.write_json("data/control/daemon_last_live_decision.json", live_dec)
    return {"mode_truth": mode_truth, "blockers": blockers}


def write_all_daemon_live_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    auth = build_daemon_live_switch_authority(runtime_root=root)
    extra = write_daemon_mode_and_start_artifacts(runtime_root=root, authority=auth)
    return {"authority": auth, **extra}


def assert_daemon_live_allowed_or_raise(
    *,
    runtime_root: Path,
    require_autonomous: bool,
) -> None:
    """Hard gate for live paths — raises RuntimeError with explicit reason."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    if not auth:
        raise RuntimeError("daemon_live_switch_authority_missing_run_closure")
    cons = build_daemon_runtime_consistency_truth(runtime_root=runtime_root, stored_authority=auth)
    if not cons.get("consistent_with_authoritative_artifacts"):
        raise RuntimeError(cons.get("exact_do_not_run_reason_if_inconsistent") or "daemon_runtime_inconsistent")
    if require_autonomous:
        if not auth.get("avenue_a_can_run_autonomous_live_now"):
            raise RuntimeError("autonomous_live_not_allowed:" + str(auth.get("exact_do_not_run_reason_if_false")))
    else:
        if not auth.get("avenue_a_can_run_supervised_live_now"):
            raise RuntimeError("supervised_live_not_allowed:" + str(auth.get("exact_do_not_run_reason_if_false")))


def build_daemon_closure_summary(*, runtime_root: Path) -> Dict[str, Any]:
    """Section I — one bundle for closure writer (honest booleans + artifact paths)."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    gh = ad.read_json("data/control/gate_b_global_halt_truth.json") or {}
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    cons = ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}
    blk = ad.read_json("data/control/daemon_start_blockers.json") or {}
    seq = ad.read_json("data/control/daemon_start_sequence.json") or {}
    sup = bool(auth.get("avenue_a_can_run_supervised_live_now"))
    aut = bool(auth.get("avenue_a_can_run_autonomous_live_now"))
    blocker = ""
    if not sup:
        blocker = str(auth.get("exact_do_not_run_reason_if_false") or "supervised_false")
    elif not aut:
        blocker = "autonomous_blocked_while_supervised_may_be_ok"
    art = "data/control/daemon_live_switch_authority.json"
    return {
        "section_i_version": "daemon_closure_rollup_v2",
        "1_raw_global_halt_truth": gh,
        "2_authoritative_daemon_halt_truth": {
            "ref": "data/control/daemon_live_switch_authority.json",
            "authoritative_global_halt_classification": auth.get("authoritative_global_halt_classification"),
            "blocks_supervised": auth.get("authoritative_global_halt_blocks_supervised"),
            "blocks_autonomous": auth.get("authoritative_global_halt_blocks_autonomous"),
        },
        "3_runtime_env_consistency_truth": cons,
        "4_avenue_a_supervised_live_truth": sup,
        "5_avenue_a_autonomous_live_truth": aut,
        "6_gate_b_supervised_live_truth": bool(auth.get("gate_b_can_run_supervised_live_now")),
        "7_gate_b_autonomous_live_truth": bool(auth.get("gate_b_can_run_autonomous_live_now")),
        "8_exact_daemon_start_blockers": blk,
        "9_exact_daemon_start_sequence": seq,
        "10_final_sentence": {
            "can_supervised_live_start_now": sup,
            "can_autonomous_live_start_now": aut,
            "if_no_exact_blocker": blocker,
            "daemon_must_trust_artifact_path": art,
        },
        "raw_global_halt_truth_ref": "data/control/gate_b_global_halt_truth.json (includes persisted_mode + classification)",
        "authoritative_daemon_halt_truth_ref": "data/control/daemon_live_switch_authority.json",
        "runtime_env_consistency_ref": "data/control/daemon_runtime_consistency_truth.json",
        "avenue_a_supervised_live": sup,
        "avenue_a_autonomous_live": aut,
        "gate_b_supervised_live": bool(auth.get("gate_b_can_run_supervised_live_now")),
        "gate_b_autonomous_live": bool(auth.get("gate_b_can_run_autonomous_live_now")),
        "daemon_start_blockers_ref": "data/control/daemon_start_blockers.json",
        "daemon_start_sequence_ref": "data/control/daemon_start_sequence.json",
        "final_sentence": {
            "can_supervised_live_start_now": sup,
            "can_autonomous_live_start_now": aut,
            "if_no_exact_blocker": blocker,
            "daemon_must_trust_artifact": art,
        },
        "global_halt_raw_vs_authoritative": {
            "raw_persisted_mode_in_operating_mode_state": gh.get("global_halt_source", {}),
            "authoritative_blocks_switch": gh.get("global_halt_is_currently_authoritative"),
            "classification": gh.get("global_halt_primary_classification"),
        },
    }
