"""Deterministic autonomous verification proof bundle — no orders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _minimal_rt(tmp_path: Path) -> Path:
    (tmp_path / "data/control").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_proof_bundle_reads_runtime_verification_and_marks_test_harness_not_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rt = _minimal_rt(tmp_path)
    ad_mod = __import__("trading_ai.storage.storage_adapter", fromlist=["LocalStorageAdapter"])
    ad = ad_mod.LocalStorageAdapter(runtime_root=rt)
    ad.write_json(
        "data/control/runtime_runner_daemon_verification.json",
        {
            "lock_exclusivity_verified": True,
            "failure_stop_verified": True,
            "verification_source": "unit_test_harness",
        },
    )
    ad.write_json(
        "data/control/universal_execution_loop_proof.json",
        {"last_trade_id": "t1", "final_execution_proven": True},
    )
    ad.write_json(
        "data/control/avenue_a_daemon_loop_emit_stamp.json",
        {
            "truth_version": "avenue_a_daemon_loop_emit_stamp_v1",
            "trade_id": "t1",
            "runtime_root": str(rt.resolve()),
            "execution_surface": "avenue_a_daemon",
            "env_fingerprint_at_emit": __import__(
                "trading_ai.orchestration.daemon_live_authority", fromlist=["compute_env_fingerprint"]
            ).compute_env_fingerprint(),
        },
    )
    from trading_ai.orchestration.autonomous_verification_proofs import (
        write_autonomous_verification_proof_bundle,
        write_daemon_context_loop_proof,
        write_daemon_failure_stop_runtime_proof,
        write_daemon_lock_exclusivity_runtime_proof,
    )

    ctx = write_daemon_context_loop_proof(runtime_root=rt)
    assert ctx.get("daemon_context_loop_proven") is True

    fs = write_daemon_failure_stop_runtime_proof(runtime_root=rt)
    assert fs.get("policy_failure_stop_flag") is True
    assert fs.get("runtime_observed_failure_stop_verified") is False

    lk = write_daemon_lock_exclusivity_runtime_proof(runtime_root=rt)
    assert lk.get("policy_lock_exclusivity_flag") is True
    assert lk.get("runtime_observed_lock_exclusivity_verified") is False

    bundle = write_autonomous_verification_proof_bundle(runtime_root=rt)
    assert bundle.get("truth_version") == "autonomous_verification_proof_bundle_v1"
    assert bundle.get("all_runtime_components_verified") is False

    raw = json.loads((rt / "data/control/autonomous_verification_proof_bundle.json").read_text(encoding="utf-8"))
    assert raw["aggregate_digest"] == bundle["aggregate_digest"]


def test_cycle_verification_artifact_uses_only_live_only_counter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rt = _minimal_rt(tmp_path)
    ad_mod = __import__("trading_ai.storage.storage_adapter", fromlist=["LocalStorageAdapter"])
    ad = ad_mod.LocalStorageAdapter(runtime_root=rt)
    ad.write_json(
        "data/control/avenue_a_daemon_state.json",
        {
            "consecutive_autonomous_ok_cycles": 99,
            "consecutive_autonomous_live_only_ok_cycles": 0,
        },
    )
    from trading_ai.orchestration.avenue_a_autonomous_runtime_truth import write_avenue_a_daemon_cycle_verification

    out = write_avenue_a_daemon_cycle_verification(runtime_root=rt)
    assert int(out.get("observed_consecutive_ok_cycles") or 0) == 0


def test_normalize_drops_authoritative_when_stale_atomic_present() -> None:
    from trading_ai.orchestration.autonomous_blocker_normalization import normalize_autonomous_blockers

    out = normalize_autonomous_blockers(
        raw_blocker_inputs=[
            "stale_global_halt_classification_autonomous_forbidden",
            "authoritative_global_halt_blocks_autonomous",
        ],
        runtime_consistency_green=True,
    )
    assert "authoritative_global_halt_blocks_autonomous" not in (out.get("active_blockers") or [])
    assert "stale_global_halt_classification_autonomous_forbidden" in (out.get("active_blockers") or [])
