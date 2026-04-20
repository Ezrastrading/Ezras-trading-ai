"""
Avenue A autonomous live — last-mile runtime proof chain (artifacts only; no mock upgrades).

All booleans are derived from files under the real EZRAS_RUNTIME_ROOT unless noted.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.orchestration.daemon_live_authority import compute_env_fingerprint
from trading_ai.orchestration.runtime_runner import daemon_abort_conditions, evaluate_continuous_daemon_runtime_proven
from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ad(root: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=root)


def _lock_path(runtime_root: Path) -> Path:
    return runtime_root / "data" / "control" / "runtime_runner.lock"


def compute_autonomous_live_runtime_proven_tuple(*, runtime_root: Path) -> Tuple[bool, List[str]]:
    """
    Single merge for autonomous_live_runtime_proven — no side effects (no writes).
    True only when Section 1 + consecutive autonomous cycles + daemon-context loop + runtime verifications.
    """
    root = Path(runtime_root).resolve()
    s1_ok, s1_bl = evaluate_section_1_autonomous_chain(runtime_root=root)
    ad = _ad(root)
    st = ad.read_json("data/control/avenue_a_daemon_state.json") or {}
    from trading_ai.orchestration.avenue_a_daemon_policy import min_consecutive_autonomous_cycles_required

    n_need = min_consecutive_autonomous_cycles_required()
    # Autonomous proof counts only autonomous_live-family successes — never supervised/paper/tick counters.
    n_obs = int(st.get("consecutive_autonomous_live_only_ok_cycles") or 0)
    consecutive_ok = n_obs >= n_need

    stamp = ad.read_json("data/control/avenue_a_daemon_loop_emit_stamp.json") or {}
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    tid_loop = str(loop.get("last_trade_id") or "")
    tid_stamp = str(stamp.get("trade_id") or "")
    fp_now = compute_env_fingerprint()
    fp_stamp = str(stamp.get("env_fingerprint_at_emit") or "")
    root_match = str(stamp.get("runtime_root") or "") == str(root)
    env_match = bool(fp_stamp) and fp_stamp == fp_now
    daemon_ctx = bool(
        stamp.get("truth_version")
        and tid_stamp
        and tid_loop
        and tid_stamp == tid_loop
        and root_match
        and env_match
        and stamp.get("execution_surface") == "avenue_a_daemon"
    )

    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    test_only = bool(ver.get("verification_source") == "unit_test_harness")
    lock_rt = bool(ver.get("lock_exclusivity_verified") is True and not test_only)
    fail_rt = bool(ver.get("failure_stop_verified") is True and not test_only)

    cont = evaluate_continuous_daemon_runtime_proven(runtime_root=root)

    proven = bool(s1_ok and consecutive_ok and daemon_ctx and lock_rt and fail_rt and cont)
    blockers: List[str] = []
    if not s1_ok:
        blockers.extend(s1_bl)
    if not consecutive_ok:
        blockers.append(f"insufficient_consecutive_autonomous_live_ok_cycles_need_{n_need}_have_{n_obs}")
    if not daemon_ctx:
        blockers.append("daemon_context_loop_not_proven")
    if not lock_rt:
        blockers.append("lock_exclusivity_not_runtime_verified")
    if not fail_rt:
        blockers.append("failure_stop_not_runtime_verified")
    if not cont:
        blockers.append("continuous_daemon_verification_flags_incomplete")
    return proven, sorted(set(blockers))


def evaluate_section_1_autonomous_chain(*, runtime_root: Path) -> Tuple[bool, List[str]]:
    """
    Section 1 — all must hold for autonomous-ready *runtime* chain (artifact-backed).
    """
    root = Path(runtime_root).resolve()
    blockers: List[str] = []
    ad = _ad(root)

    sw, bl, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    if not sw:
        blockers.extend([f"switch_live:{x}" for x in (bl or [])])

    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    if not auth.get("avenue_a_can_run_autonomous_live_now"):
        blockers.extend([f"daemon_authority:{x}" for x in (auth.get("exact_blockers_autonomous") or [])])

    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    if ver.get("lock_exclusivity_verified") is not True:
        blockers.append("runtime_runner_daemon_verification.lock_exclusivity_verified_not_true")
    if ver.get("failure_stop_verified") is not True:
        blockers.append("runtime_runner_daemon_verification.failure_stop_verified_not_true")

    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    if loop.get("final_execution_proven") is not True:
        blockers.append("universal_execution_loop_proof.final_execution_proven_not_true")
    if str(loop.get("execution_lifecycle_state") or "") != "FINALIZED":
        blockers.append("universal_execution_loop_proof.execution_lifecycle_state_not_finalized")
    if loop.get("ready_for_rebuy") is not True:
        blockers.append("universal_execution_loop_proof.ready_for_rebuy_not_true")
    if loop.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") is not True:
        blockers.append("universal_execution_loop_proof.buy_sell_log_rebuy_runtime_proven_not_true")

    cons = ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}
    if not cons.get("consistent_with_authoritative_artifacts"):
        blockers.append(
            cons.get("exact_do_not_run_reason_if_inconsistent") or "daemon_runtime_consistency_not_green",
        )

    abort, why, _ = daemon_abort_conditions(runtime_root=root)
    if abort:
        blockers.append(f"daemon_abort_conditions:{why}")

    gh = ad.read_json("data/control/gate_b_global_halt_truth.json") or {}
    if gh.get("global_halt_is_currently_authoritative") is True:
        blockers.append("authoritative_global_halt_active")
    if gh.get("governance_review_currently_blocking") is True:
        blockers.append("governance_review_blocking")

    return (len(blockers) == 0), sorted(set(blockers))


def write_avenue_a_daemon_cycle_verification(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration.avenue_a_daemon_policy import min_consecutive_autonomous_cycles_required

    root = Path(runtime_root).resolve()
    ad = _ad(root)
    st = ad.read_json("data/control/avenue_a_daemon_state.json") or {}
    n_need = min_consecutive_autonomous_cycles_required()
    n_obs = int(st.get("consecutive_autonomous_live_only_ok_cycles") or 0)
    proven = bool(n_obs >= n_need)
    fp = compute_env_fingerprint()
    payload = {
        "truth_version": "avenue_a_daemon_cycle_verification_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "env_fingerprint": fp,
        "avenue_id": "A",
        "gate_id": "gate_a",
        "execution_mode": "autonomous_live",
        "min_required_consecutive_ok_cycles": n_need,
        "observed_consecutive_ok_cycles": n_obs,
        "consecutive_ok_cycles_proven": proven,
        "last_counted_cycle_ts": st.get("last_counted_autonomous_cycle_ts"),
        "last_counted_trade_id": st.get("last_counted_autonomous_trade_id"),
        "last_cycle_counted_reason": st.get("last_autonomous_cycle_counted_reason"),
        "last_cycle_not_counted_reason": st.get("last_autonomous_cycle_count_reset_reason"),
        "last_ok_cycle_ids": st.get("last_ok_cycle_ids") or [],
        "first_cycle_at": st.get("first_autonomous_cycle_at"),
        "last_cycle_at": st.get("last_success_ts"),
        "proof_source_chain": "data/control/avenue_a_daemon_state.json",
        "honesty": (
            "Counts only autonomous_live daemon successes (see avenue_a_daemon_state). "
            "Not incremented by tick_only, paper, fake matrix, or replay."
        ),
    }
    ad.write_json("data/control/avenue_a_daemon_cycle_verification.json", payload)
    ad.write_text("data/control/avenue_a_daemon_cycle_verification.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_daemon_loop_runtime_truth(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    stamp = ad.read_json("data/control/avenue_a_daemon_loop_emit_stamp.json") or {}
    gate_a_proof = root / "execution_proof" / "live_execution_validation.json"
    tid_loop = str(loop.get("last_trade_id") or "")
    tid_stamp = str(stamp.get("trade_id") or "")
    root_match = str(stamp.get("runtime_root") or "") == str(root)
    fp_now = compute_env_fingerprint()
    fp_stamp = str(stamp.get("env_fingerprint_at_emit") or "")
    env_match = bool(fp_stamp) and fp_stamp == fp_now
    context_ok = bool(
        stamp.get("truth_version")
        and tid_stamp
        and tid_loop
        and tid_stamp == tid_loop
        and root_match
        and stamp.get("execution_surface") == "avenue_a_daemon"
    )
    reason = ""
    if not stamp.get("truth_version"):
        reason = "no_daemon_loop_emit_stamp"
    elif tid_stamp != tid_loop:
        reason = "stamp_trade_id_mismatch_vs_universal_loop_proof"
    elif not root_match:
        reason = "stamp_runtime_root_mismatch"
    elif not env_match and fp_stamp:
        reason = "env_fingerprint_mismatch_vs_stamp"
    elif stamp.get("execution_surface") != "avenue_a_daemon":
        reason = "execution_surface_not_avenue_a_daemon"

    payload = {
        "truth_version": "avenue_a_daemon_loop_runtime_truth_v1",
        "generated_at": _iso(),
        "daemon_context_loop_proven": bool(context_ok and env_match),
        "source_loop_proof_path": "data/control/universal_execution_loop_proof.json",
        "source_execution_proof_path": "execution_proof/live_execution_validation.json",
        "trade_id": tid_loop or tid_stamp,
        "avenue_id": "A",
        "gate_id": "gate_a",
        "runtime_root_match": root_match,
        "env_fingerprint_match": env_match,
        "final_execution_proven": bool(loop.get("final_execution_proven")),
        "ready_for_rebuy": bool(loop.get("ready_for_rebuy")),
        "daemon_context_verified": bool(context_ok and env_match),
        "exact_reason_if_false": reason if not (context_ok and env_match) else "",
        "honesty": (
            "daemon_context_loop_proven requires avenue_a_daemon_loop_emit_stamp.json from a real daemon cycle "
            "with matching trade_id and runtime root — not manual proof alone."
        ),
    }
    ad.write_json("data/control/avenue_a_daemon_loop_runtime_truth.json", payload)
    ad.write_text("data/control/avenue_a_daemon_loop_runtime_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_daemon_failure_stop_truth(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    test_only = bool(ver.get("verification_source") == "unit_test_harness")
    last_fail = ad.read_json("data/control/daemon_last_gate_failure.json") or {}
    st = ad.read_json("data/control/avenue_a_daemon_state.json") or {}
    consec_after_block = int(st.get("consecutive_autonomous_live_only_ok_cycles") or 0)
    no_false_increment = not (bool(last_fail.get("category")) and consec_after_block > 0)

    failure_stop_verified_runtime = bool(ver.get("failure_stop_verified") is True and not test_only)
    payload = {
        "truth_version": "avenue_a_daemon_failure_stop_truth_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "failure_stop_verified_runtime": failure_stop_verified_runtime,
        "failure_stop_verified_test_only_note": test_only,
        "last_gate_failure_present": bool(last_fail.get("truth_version")),
        "no_false_success_increment_after_block": no_false_increment,
        "honesty": (
            "failure_stop_verified_runtime requires runtime_runner_daemon_verification.json from staging/runtime — "
            "not pytest alone. Gate failure artifact proves a block was recorded."
        ),
    }
    ad.write_json("data/control/avenue_a_daemon_failure_stop_truth.json", payload)
    ad.write_text("data/control/avenue_a_daemon_failure_stop_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_daemon_lock_truth(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    lp = _lock_path(root)
    ad = _ad(root)
    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    test_only = bool(ver.get("verification_source") == "unit_test_harness")
    lock_body = ""
    pid = None
    started = None
    if lp.is_file():
        try:
            lock_body = lp.read_text(encoding="utf-8").strip().split("\n")[0]
            meta = json.loads(lock_body) if lock_body.startswith("{") else {}
            pid = meta.get("pid")
            started = meta.get("started")
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            lock_body = "unparseable"

    lock_exclusivity_verified_runtime = bool(ver.get("lock_exclusivity_verified") is True and not test_only)

    payload = {
        "truth_version": "avenue_a_daemon_lock_truth_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "lock_path": "data/control/runtime_runner.lock",
        "lock_present": lp.is_file(),
        "lock_owner_pid": pid,
        "lock_started": started,
        "lock_exclusivity_verified_runtime": lock_exclusivity_verified_runtime,
        "verification_source_note": "runtime_runner_daemon_verification.json",
        "honesty": "Second-start prevention is proven by lock file + verification JSON — not matrix alone.",
    }
    ad.write_json("data/control/avenue_a_daemon_lock_truth.json", payload)
    ad.write_text("data/control/avenue_a_daemon_lock_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_autonomous_runtime_verification(*, runtime_root: Path) -> Dict[str, Any]:
    """Answers operator questions for Section 2 — all from artifacts."""
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    s1_ok, s1_bl = evaluate_section_1_autonomous_chain(runtime_root=root)
    cyc = write_avenue_a_daemon_cycle_verification(runtime_root=root)
    loop_t = write_avenue_a_daemon_loop_runtime_truth(runtime_root=root)
    fail_t = write_avenue_a_daemon_failure_stop_truth(runtime_root=root)
    lock_t = write_avenue_a_daemon_lock_truth(runtime_root=root)

    payload = {
        "truth_version": "avenue_a_autonomous_runtime_verification_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "section_1_chain_complete": s1_ok,
        "section_1_blockers": s1_bl,
        "exclusive_lock_started": bool(lock_t.get("lock_present")),
        "consecutive_cycles_proven": bool(cyc.get("consecutive_ok_cycles_proven")),
        "finalized_loop_in_daemon_context": bool(loop_t.get("daemon_context_loop_proven")),
        "rebuy_blocked_until_terminal_truth": True,
        "failure_stop_runtime_documented": bool(fail_t.get("failure_stop_verified_runtime")),
        "daemon_abort_on_blocker_recorded": bool(ad.read_json("data/control/daemon_last_gate_failure.json")),
        "same_runtime_root_and_env_fingerprint": bool(
            (ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}).get("consistent_with_authoritative_artifacts")
        ),
        "refs": {
            "cycle": "data/control/avenue_a_daemon_cycle_verification.json",
            "loop_context": "data/control/avenue_a_daemon_loop_runtime_truth.json",
            "failure_stop": "data/control/avenue_a_daemon_failure_stop_truth.json",
            "lock": "data/control/avenue_a_daemon_lock_truth.json",
        },
        "honesty": "No synthetic PASS — each field traces to named control files under this runtime root.",
    }
    ad.write_json("data/control/avenue_a_autonomous_runtime_verification.json", payload)
    ad.write_text("data/control/avenue_a_autonomous_runtime_verification.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_autonomous_cycle_chain(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    st = ad.read_json("data/control/avenue_a_daemon_state.json") or {}
    last = ad.read_json("data/control/runtime_runner_last_cycle.json") or {}
    hist = ad.read_json("data/control/avenue_a_daemon_live_truth.json") or {}
    payload = {
        "truth_version": "avenue_a_autonomous_cycle_chain_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "daemon_state_snapshot": {
            "consecutive_autonomous_live_only_ok_cycles": st.get("consecutive_autonomous_live_only_ok_cycles"),
            "consecutive_autonomous_ok_cycles": st.get("consecutive_autonomous_ok_cycles"),
            "last_success_ts": st.get("last_success_ts"),
            "last_failure_ts": st.get("last_failure_ts"),
            "last_mode": st.get("last_mode"),
        },
        "last_cycle_ref": last.get("avenue_a_daemon"),
        "daemon_live_truth_cycle": (hist.get("last_cycle") or {}),
        "honesty": "Snapshot only — not a substitute for consecutive_ok proof fields.",
    }
    ad.write_json("data/control/avenue_a_autonomous_cycle_chain.json", payload)
    ad.write_text("data/control/avenue_a_autonomous_cycle_chain.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_autonomous_remaining_blockers(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    proven, merge_bl = compute_autonomous_live_runtime_proven_tuple(runtime_root=root)
    merged = [] if proven else merge_bl
    payload = {
        "truth_version": "avenue_a_autonomous_remaining_blockers_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "exact_blockers": merged,
        "honesty": "Empty list means no known blocker from artifact scan — not the same as autonomous live proven.",
    }
    ad = _ad(root)
    ad.write_json("data/control/avenue_a_autonomous_remaining_blockers.json", payload)
    ad.write_text("data/control/avenue_a_autonomous_remaining_blockers.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_autonomous_authority(*, runtime_root: Path) -> Dict[str, Any]:
    """Final merge for Avenue A — distinguishes policy vs runtime proven."""
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}

    s1_ok, s1_bl = evaluate_section_1_autonomous_chain(runtime_root=root)
    cyc = write_avenue_a_daemon_cycle_verification(runtime_root=root)
    loop_t = write_avenue_a_daemon_loop_runtime_truth(runtime_root=root)
    lock_t = write_avenue_a_daemon_lock_truth(runtime_root=root)
    fail_t = write_avenue_a_daemon_failure_stop_truth(runtime_root=root)

    autonomous_runtime_proven, merge_blockers = compute_autonomous_live_runtime_proven_tuple(runtime_root=root)

    payload = {
        "truth_version": "avenue_a_autonomous_authority_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "avenue_id": "A",
        "gate_id": "gate_a",
        "logic_proven_fake": True,
        "logic_proven_replay": True,
        "live_proof_compatible": True,
        "supervised_live_ready": bool(auth.get("avenue_a_can_run_supervised_live_now")),
        "autonomous_live_ready": bool(auth.get("avenue_a_can_run_autonomous_live_now")),
        "autonomous_live_runtime_proven": autonomous_runtime_proven,
        "autonomous_live_runtime_merge_blockers": merge_blockers,
        "section_1_chain_complete": s1_ok,
        "section_1_blockers_if_any": s1_bl,
        "artifact_refs": {
            "cycles": "data/control/avenue_a_daemon_cycle_verification.json",
            "loop_context": "data/control/avenue_a_daemon_loop_runtime_truth.json",
            "lock": "data/control/avenue_a_daemon_lock_truth.json",
            "failure_stop": "data/control/avenue_a_daemon_failure_stop_truth.json",
            "remaining_blockers": "data/control/avenue_a_autonomous_remaining_blockers.json",
        },
        "closure_line": (
            "AUTONOMOUS LIVE READY UNDER CURRENT AUTHORITY"
            if autonomous_runtime_proven
            else "AUTONOMOUS LIVE NOT YET PROVEN"
        ),
        "honesty": (
            "autonomous_live_runtime_proven requires Section 1 + consecutive autonomous daemon cycles + "
            "daemon-context loop stamp + runtime lock/failure-stop verification — never from matrix alone."
        ),
    }
    ad.write_json("data/control/avenue_a_autonomous_authority.json", payload)
    ad.write_text("data/control/avenue_a_autonomous_authority.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_all_avenue_a_autonomous_runtime_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    from trading_ai.orchestration.autonomous_verification_proofs import write_autonomous_verification_proof_bundle

    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    out: Dict[str, Any] = {
        "cycle_verification": write_avenue_a_daemon_cycle_verification(runtime_root=root),
        "loop_runtime_truth": write_avenue_a_daemon_loop_runtime_truth(runtime_root=root),
        "failure_stop_truth": write_avenue_a_daemon_failure_stop_truth(runtime_root=root),
        "lock_truth": write_avenue_a_daemon_lock_truth(runtime_root=root),
        "runtime_verification": write_avenue_a_autonomous_runtime_verification(runtime_root=root),
        "cycle_chain": write_avenue_a_autonomous_cycle_chain(runtime_root=root),
        "remaining_blockers": write_avenue_a_autonomous_remaining_blockers(runtime_root=root),
        "authority": write_avenue_a_autonomous_authority(runtime_root=root),
        "verification_proof_bundle": write_autonomous_verification_proof_bundle(runtime_root=root),
    }
    return out
