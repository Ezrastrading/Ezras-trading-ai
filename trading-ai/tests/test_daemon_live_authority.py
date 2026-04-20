"""Daemon live authority: consistency, halt interpretation, Gate B not bypassing daemon truth."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _minimal_switch_a(rt: Path) -> None:
    from trading_ai.control.system_execution_lock import save_system_execution_lock

    (rt / "data/control").mkdir(parents=True, exist_ok=True)
    save_system_execution_lock(
        {
            "system_locked": True,
            "ready_for_live_execution": True,
            "gate_a_enabled": True,
            "gate_b_enabled": True,
            "safety_checks": {
                "policy_aligned": True,
                "capital_truth_valid": True,
                "artifacts_writing": True,
                "supabase_connected": True,
            },
        },
        runtime_root=rt,
    )
    (rt / "data/control/go_no_go_decision.json").write_text(
        json.dumps({"ready_for_first_5_trades": True}), encoding="utf-8"
    )
    (rt / "data/control/execution_mirror_results.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (rt / "data/control/operator_live_confirmation.json").write_text(
        json.dumps({"confirmed": True}), encoding="utf-8"
    )


def test_runtime_root_mismatch_blocks_consistency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.orchestration import daemon_live_authority as dla

    (tmp_path / "data/control").mkdir(parents=True, exist_ok=True)
    snap = {
        "authoritative_runtime_root": "/wrong/root",
        "authoritative_env_fingerprint": dla.compute_env_fingerprint(),
    }
    out = dla.build_daemon_runtime_consistency_truth(runtime_root=tmp_path, stored_authority=snap)
    assert out["consistent_with_authoritative_artifacts"] is False
    assert "mismatch" in (out.get("exact_do_not_run_reason_if_inconsistent") or "").lower()


def test_env_fingerprint_mismatch_blocks_consistency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_DRY_RUN", "1")
    from trading_ai.orchestration import daemon_live_authority as dla

    (tmp_path / "data/control").mkdir(parents=True, exist_ok=True)
    fp_wrong = "0" * 56
    snap = {
        "authoritative_runtime_root": str(tmp_path.resolve()),
        "authoritative_env_fingerprint": fp_wrong,
    }
    out = dla.build_daemon_runtime_consistency_truth(runtime_root=tmp_path, stored_authority=snap)
    assert out["consistent_with_authoritative_artifacts"] is False
    assert "env_fingerprint" in (out.get("mismatched_keys") or [])


def test_live_execution_gate_reads_authority_not_raw_halted_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _minimal_switch_a(tmp_path)
    from trading_ai.orchestration import daemon_live_authority as dla
    from trading_ai.orchestration import runtime_runner as rr

    (tmp_path / "data/control/operating_mode_state.json").write_text(
        json.dumps({"mode": "halted"}), encoding="utf-8"
    )
    auth = {
        "authoritative_runtime_root": str(tmp_path.resolve()),
        "authoritative_env_fingerprint": dla.compute_env_fingerprint(),
        "avenue_a_can_run_supervised_live_now": True,
        "avenue_a_can_run_autonomous_live_now": False,
    }
    (tmp_path / "data/control/daemon_live_switch_authority.json").write_text(
        json.dumps(auth), encoding="utf-8"
    )
    ok, bl = rr.live_execution_gate_ok(runtime_root=tmp_path, daemon_live_tier="supervised")
    assert ok is True
    assert bl == []


def test_stale_non_authoritative_halt_supervised_may_pass_autonomous_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _minimal_switch_a(tmp_path)
    from trading_ai.orchestration import daemon_live_authority as dla

    gh = {
        "truth_version": "gate_b_global_halt_truth_v1",
        "global_halt_primary_classification": "STALE_PERSISTED_STATE",
        "global_halt_is_stale": True,
        "global_halt_is_currently_authoritative": False,
        "governance_review_currently_blocking": False,
        "operator_governance_ack_present": False,
        "exact_do_not_go_live_reason_if_false": None,
        "honesty": "test",
    }
    with patch(
        "trading_ai.orchestration.daemon_live_authority._read_gate_b_halt",
        lambda _ad: gh,
    ):
        with patch(
            "trading_ai.orchestration.daemon_live_authority._build_gate_b_final_go_live_truth",
            lambda **_k: {"gate_b_can_be_switched_live_now": True},
        ):
            with patch(
                "trading_ai.orchestration.switch_live.compute_avenue_switch_live_now",
                lambda *_a, **_k: (True, [], {}),
            ):
                with patch(
                    "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_supervised_inputs_ok",
                    lambda **_k: (True, "ok"),
                ):
                    with patch(
                        "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_autonomous_runtime_proven",
                        lambda **_k: (True, "ok"),
                    ):
                        with patch(
                            "trading_ai.orchestration.runtime_runner.evaluate_continuous_daemon_runtime_proven",
                            lambda **_k: True,
                        ):
                            ad = __import__(
                                "trading_ai.storage.storage_adapter", fromlist=["LocalStorageAdapter"]
                            ).LocalStorageAdapter(runtime_root=tmp_path)
                            ad.write_json(
                                "data/control/universal_execution_loop_proof.json",
                                {
                                    "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": True,
                                    "final_execution_proven": True,
                                    "execution_lifecycle_state": "FINALIZED",
                                },
                            )
                            ad.write_json("data/control/operating_mode_state.json", {"mode": "halted"})
                            payload = dla.build_daemon_live_switch_authority(runtime_root=tmp_path)
    assert payload["avenue_a_can_run_supervised_live_now"] is True
    assert payload["avenue_a_can_run_autonomous_live_now"] is False
    assert payload["authoritative_global_halt_blocks_autonomous"] is False
    assert "stale_global_halt_classification_autonomous_forbidden" in (payload.get("exact_blockers_autonomous") or [])
    assert payload.get("autonomous_halt_audit", {}).get("stale_halt_evidence_detected") is True


def test_authoritative_global_halt_blocks_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _minimal_switch_a(tmp_path)
    from trading_ai.orchestration import daemon_live_authority as dla

    gh = {
        "truth_version": "gate_b_global_halt_truth_v1",
        "global_halt_primary_classification": "REAL_CURRENT_GLOBAL_RISK",
        "global_halt_is_stale": False,
        "global_halt_is_currently_authoritative": True,
        "governance_review_currently_blocking": False,
        "operator_governance_ack_present": False,
    }
    with patch(
        "trading_ai.orchestration.daemon_live_authority._read_gate_b_halt",
        lambda _ad: gh,
    ):
        with patch(
            "trading_ai.orchestration.daemon_live_authority._build_gate_b_final_go_live_truth",
            lambda **_k: {"gate_b_can_be_switched_live_now": True},
        ):
            with patch(
                "trading_ai.orchestration.switch_live.compute_avenue_switch_live_now",
                lambda *_a, **_k: (True, [], {}),
            ):
                with patch(
                    "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_supervised_inputs_ok",
                    lambda **_k: (True, "ok"),
                ):
                    with patch(
                        "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_autonomous_runtime_proven",
                        lambda **_k: (True, "ok"),
                    ):
                        with patch(
                            "trading_ai.orchestration.runtime_runner.evaluate_continuous_daemon_runtime_proven",
                            lambda **_k: True,
                        ):
                            __import__(
                                "trading_ai.storage.storage_adapter", fromlist=["LocalStorageAdapter"]
                            ).LocalStorageAdapter(runtime_root=tmp_path).write_json(
                                "data/control/universal_execution_loop_proof.json",
                                {
                                    "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": True,
                                    "final_execution_proven": True,
                                    "execution_lifecycle_state": "FINALIZED",
                                },
                            )
                            __import__(
                                "trading_ai.storage.storage_adapter", fromlist=["LocalStorageAdapter"]
                            ).LocalStorageAdapter(runtime_root=tmp_path).write_json(
                                "data/control/operating_mode_state.json", {"mode": "halted"}
                            )
                            payload = dla.build_daemon_live_switch_authority(runtime_root=tmp_path)
    assert payload["avenue_a_can_run_supervised_live_now"] is False
    assert payload["avenue_a_can_run_autonomous_live_now"] is False


def test_gate_b_gb_can_true_does_not_bypass_daemon_halt_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _minimal_switch_a(tmp_path)
    from trading_ai.orchestration import daemon_live_authority as dla

    gh = {
        "truth_version": "gate_b_global_halt_truth_v1",
        "global_halt_primary_classification": "REAL_CURRENT_GLOBAL_RISK",
        "global_halt_is_stale": False,
        "global_halt_is_currently_authoritative": True,
        "governance_review_currently_blocking": False,
        "operator_governance_ack_present": False,
    }
    with patch(
        "trading_ai.orchestration.daemon_live_authority._read_gate_b_halt",
        lambda _ad: gh,
    ):
        with patch(
            "trading_ai.orchestration.daemon_live_authority._build_gate_b_final_go_live_truth",
            lambda **_k: {"gate_b_can_be_switched_live_now": True},
        ):
            with patch(
                "trading_ai.orchestration.switch_live.compute_avenue_switch_live_now",
                lambda *_a, **_k: (True, [], {}),
            ):
                with patch(
                    "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_supervised_inputs_ok",
                    lambda **_k: (True, "ok"),
                ):
                    with patch(
                        "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_autonomous_runtime_proven",
                        lambda **_k: (True, "ok"),
                    ):
                        with patch(
                            "trading_ai.orchestration.runtime_runner.evaluate_continuous_daemon_runtime_proven",
                            lambda **_k: True,
                        ):
                            __import__(
                                "trading_ai.storage.storage_adapter", fromlist=["LocalStorageAdapter"]
                            ).LocalStorageAdapter(runtime_root=tmp_path).write_json(
                                "data/control/universal_execution_loop_proof.json",
                                {
                                    "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": True,
                                    "final_execution_proven": True,
                                    "execution_lifecycle_state": "FINALIZED",
                                },
                            )
                            __import__(
                                "trading_ai.storage.storage_adapter", fromlist=["LocalStorageAdapter"]
                            ).LocalStorageAdapter(runtime_root=tmp_path).write_json(
                                "data/control/operating_mode_state.json", {"mode": "halted"}
                            )
                            payload = dla.build_daemon_live_switch_authority(runtime_root=tmp_path)
    assert payload["gate_b_can_run_supervised_live_now"] is False


def test_fingerprint_stable_for_equivalent_ezr_as_runtime_root_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equivalent ``EZRAS_RUNTIME_ROOT`` spellings must hash identically (canonical resolved path)."""
    monkeypatch.setenv("COINBASE_ENABLED", "1")
    from trading_ai.orchestration import daemon_live_authority as dla

    canon = str(tmp_path.resolve())
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", canon)
    fp_a = dla.compute_env_fingerprint()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", canon + "/")
    fp_b = dla.compute_env_fingerprint()
    assert fp_a == fp_b


def test_consistency_truth_lists_env_drift_keys_when_snapshot_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("COINBASE_ENABLED", "0")
    from trading_ai.orchestration import daemon_live_authority as dla

    inputs_before = dla.compute_env_fingerprint_inputs()
    fp_before = dla.compute_env_fingerprint()
    monkeypatch.setenv("COINBASE_ENABLED", "1")
    fp_after = dla.compute_env_fingerprint()
    assert fp_before != fp_after

    snap = {
        "authoritative_runtime_root": str(tmp_path.resolve()),
        "authoritative_env_fingerprint": fp_before,
        "fingerprint_inputs_canonical_snapshot": inputs_before,
    }
    out = dla.build_daemon_runtime_consistency_truth(runtime_root=tmp_path, stored_authority=snap)
    assert out["consistent_with_authoritative_artifacts"] is False
    assert "COINBASE_ENABLED" in (out.get("env_fingerprint_drift_keys") or [])


def test_closure_rollup_is_honest_when_missing_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data/control").mkdir(parents=True, exist_ok=True)
    from trading_ai.orchestration import daemon_live_authority as dla

    rollup = dla.build_daemon_closure_summary(runtime_root=tmp_path)
    assert rollup["10_final_sentence"]["can_supervised_live_start_now"] is False
    assert rollup["10_final_sentence"]["can_autonomous_live_start_now"] is False
