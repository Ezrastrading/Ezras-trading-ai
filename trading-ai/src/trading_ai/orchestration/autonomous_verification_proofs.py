"""
Deterministic, disk-inspectable autonomous verification proof bundle (no live orders).

Aggregates existing control artifacts into a single versioned JSON with a content digest.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.orchestration.daemon_live_authority import compute_env_fingerprint
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(obj: Dict[str, Any]) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def write_daemon_context_loop_proof(*, runtime_root: Path) -> Dict[str, Any]:
    """Proof artifact: daemon loop stamp aligns with universal loop proof + env/root."""
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    stamp = ad.read_json("data/control/avenue_a_daemon_loop_emit_stamp.json") or {}
    fp_now = compute_env_fingerprint()
    tid_loop = str(loop.get("last_trade_id") or "")
    tid_stamp = str(stamp.get("trade_id") or "")
    root_ok = str(stamp.get("runtime_root") or "") == str(root)
    fp_stamp = str(stamp.get("env_fingerprint_at_emit") or "")
    env_ok = bool(fp_stamp) and fp_stamp == fp_now
    proven = bool(
        stamp.get("truth_version")
        and tid_stamp
        and tid_loop
        and tid_stamp == tid_loop
        and root_ok
        and env_ok
        and stamp.get("execution_surface") == "avenue_a_daemon"
    )
    canonical = {
        "trade_id_loop": tid_loop,
        "trade_id_stamp": tid_stamp,
        "runtime_root_matches": root_ok,
        "env_fingerprint_matches": env_ok,
        "execution_surface": stamp.get("execution_surface"),
    }
    payload = {
        "truth_version": "daemon_context_loop_proof_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "daemon_context_loop_proven": proven,
        "canonical_inputs_digest": _digest(canonical),
        "sources": {
            "universal_loop": "data/control/universal_execution_loop_proof.json",
            "emit_stamp": "data/control/avenue_a_daemon_loop_emit_stamp.json",
        },
        "honesty": "Proven only when stamp trade_id matches loop last_trade_id and env/root match current process.",
    }
    ad.write_json("data/control/daemon_context_loop_proof.json", payload)
    return payload


def write_daemon_failure_stop_runtime_proof(*, runtime_root: Path) -> Dict[str, Any]:
    """Proof artifact: failure-stop path verified in runtime_runner_daemon_verification (not test-only)."""
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    test_only = bool(ver.get("verification_source") == "unit_test_harness")
    policy_ok = bool(ver.get("failure_stop_verified") is True)
    runtime_ok = policy_ok and not test_only
    canonical = {
        "failure_stop_verified": ver.get("failure_stop_verified"),
        "verification_source": ver.get("verification_source"),
    }
    payload = {
        "truth_version": "daemon_failure_stop_runtime_proof_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "policy_failure_stop_flag": policy_ok,
        "runtime_observed_failure_stop_verified": runtime_ok,
        "verification_source": ver.get("verification_source"),
        "canonical_inputs_digest": _digest(canonical),
        "sources": {"runtime_runner_daemon_verification": "data/control/runtime_runner_daemon_verification.json"},
        "honesty": "runtime_observed_failure_stop_verified is false when verification_source is unit_test_harness.",
    }
    ad.write_json("data/control/daemon_failure_stop_runtime_proof.json", payload)
    return payload


def write_daemon_lock_exclusivity_runtime_proof(*, runtime_root: Path) -> Dict[str, Any]:
    """Proof artifact: lock exclusivity verified in runtime verification JSON + optional lock file presence."""
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ver = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    test_only = bool(ver.get("verification_source") == "unit_test_harness")
    lp = root / "data" / "control" / "runtime_runner.lock"
    policy_ok = bool(ver.get("lock_exclusivity_verified") is True)
    runtime_ok = policy_ok and not test_only
    canonical = {
        "lock_exclusivity_verified": ver.get("lock_exclusivity_verified"),
        "verification_source": ver.get("verification_source"),
        "lock_file_present": lp.is_file(),
    }
    payload = {
        "truth_version": "daemon_lock_exclusivity_runtime_proof_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "policy_lock_exclusivity_flag": policy_ok,
        "runtime_observed_lock_exclusivity_verified": runtime_ok,
        "lock_file_present": lp.is_file(),
        "canonical_inputs_digest": _digest(canonical),
        "sources": {
            "runtime_runner_daemon_verification": "data/control/runtime_runner_daemon_verification.json",
            "lock_path": "data/control/runtime_runner.lock",
        },
        "honesty": "Exclusivity for live runner is proven by staged verification JSON — lock file alone is not sufficient.",
    }
    ad.write_json("data/control/daemon_lock_exclusivity_runtime_proof.json", payload)
    return payload


def write_autonomous_verification_proof_bundle(*, runtime_root: Path) -> Dict[str, Any]:
    """Single bundle referencing loop, failure-stop, and lock proofs with aggregate digest."""
    root = Path(runtime_root).resolve()
    ctx = write_daemon_context_loop_proof(runtime_root=root)
    fs = write_daemon_failure_stop_runtime_proof(runtime_root=root)
    lk = write_daemon_lock_exclusivity_runtime_proof(runtime_root=root)
    ad = LocalStorageAdapter(runtime_root=root)
    agg = {
        "daemon_context_loop_proven": ctx.get("daemon_context_loop_proven"),
        "failure_stop_runtime": fs.get("runtime_observed_failure_stop_verified"),
        "lock_exclusivity_runtime": lk.get("runtime_observed_lock_exclusivity_verified"),
    }
    payload = {
        "truth_version": "autonomous_verification_proof_bundle_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "aggregate_digest": _digest(agg),
        "components": {
            "daemon_context_loop_proof": "data/control/daemon_context_loop_proof.json",
            "daemon_failure_stop_runtime_proof": "data/control/daemon_failure_stop_runtime_proof.json",
            "daemon_lock_exclusivity_runtime_proof": "data/control/daemon_lock_exclusivity_runtime_proof.json",
        },
        "all_runtime_components_verified": bool(
            agg["daemon_context_loop_proven"] and agg["failure_stop_runtime"] and agg["lock_exclusivity_runtime"]
        ),
        "honesty": "Bundle is derived from existing artifacts — it does not substitute for running staging verification.",
    }
    ad.write_json("data/control/autonomous_verification_proof_bundle.json", payload)
    return payload
