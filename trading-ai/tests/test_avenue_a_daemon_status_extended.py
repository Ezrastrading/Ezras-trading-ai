"""avenue-a-daemon-status: supervised vs historical failure clarity."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_status_notes_historical_last_failure_when_gate_a_proof_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    from trading_ai.orchestration import daemon_live_authority as dla

    fp = dla.compute_env_fingerprint()
    snap_in = dla.compute_env_fingerprint_inputs()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True)
    (root / "execution_proof").mkdir(parents=True)
    (root / "execution_proof" / "live_execution_validation.json").write_text(
        json.dumps(
            {
                "FINAL_EXECUTION_PROVEN": True,
                "execution_success": True,
                "runtime_root": str(root),
                "coinbase_order_verified": True,
                "databank_written": True,
                "supabase_synced": True,
                "governance_logged": True,
                "packet_updated": True,
                "scheduler_stable": True,
                "pnl_calculation_verified": True,
                "partial_failure_codes": [],
            }
        ),
        encoding="utf-8",
    )
    (ctrl / "runtime_runner_last_failure.json").write_text(
        json.dumps({"avenue_a_daemon": {"ok": False}, "failure_reason": "old"}),
        encoding="utf-8",
    )
    (ctrl / "daemon_live_switch_authority.json").write_text(
        json.dumps(
            {
                "truth_version": "daemon_live_switch_authority_v1",
                "avenue_a_can_run_supervised_live_now": True,
                "authoritative_runtime_root": str(root),
                "authoritative_env_fingerprint": fp,
                "fingerprint_inputs_canonical_snapshot": snap_in,
            }
        ),
        encoding="utf-8",
    )
    (ctrl / "daemon_enable_readiness_after_supervised.json").write_text(
        json.dumps({"avenue_a_can_enable_daemon_now": True}),
        encoding="utf-8",
    )
    (ctrl / "avenue_a_supervised_live_truth.json").write_text(
        json.dumps({"supervised_live_runtime_proven": True}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")

    with patch(
        "trading_ai.orchestration.avenue_a_live_daemon.avenue_a_supervised_runtime_allowed",
        return_value=(True, "ok"),
    ):
        from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status

        st = avenue_a_daemon_status(runtime_root=root)

    notes = (st.get("supervised") or {}).get("stale_or_historical_artifact_notes") or []
    assert any("historical_runtime_runner_last_failure" in n for n in notes)


def test_status_shows_runtime_consistency_blocker_and_refresh_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("COINBASE_ENABLED", "0")
    from trading_ai.orchestration import daemon_live_authority as dla

    snap_inputs = dla.compute_env_fingerprint_inputs()
    snap_fp = dla.compute_env_fingerprint()
    monkeypatch.setenv("COINBASE_ENABLED", "1")

    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True)
    (root / "execution_proof").mkdir(parents=True)
    (root / "execution_proof" / "live_execution_validation.json").write_text(
        json.dumps({"FINAL_EXECUTION_PROVEN": True, "execution_success": True, "runtime_root": str(root)}),
        encoding="utf-8",
    )
    (ctrl / "daemon_live_switch_authority.json").write_text(
        json.dumps(
            {
                "truth_version": "daemon_live_switch_authority_v1",
                "authoritative_runtime_root": str(root),
                "authoritative_env_fingerprint": snap_fp,
                "fingerprint_inputs_canonical_snapshot": snap_inputs,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")

    with patch(
        "trading_ai.orchestration.avenue_a_live_daemon.avenue_a_supervised_runtime_allowed",
        return_value=(False, "blocked"),
    ):
        from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status

        st = avenue_a_daemon_status(runtime_root=root)

    rc = st.get("runtime_consistency") or {}
    assert rc.get("authority_refresh_recommended") is True
    assert "refresh-supervised-daemon-truth-chain" in (rc.get("refresh_authority_command") or "")
    assert "COINBASE_ENABLED" in (rc.get("env_fingerprint_drift_keys") or [])
    steps = rc.get("canonical_operator_next_steps") or []
    assert steps and "refresh-supervised-daemon-truth-chain" in steps[0]
    blockers = st.get("current_supervised_readiness_blockers") or []
    assert any("daemon_runtime_consistency" in b for b in blockers)


def test_status_green_path_no_fingerprint_mismatch_when_authority_matches_current_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When authority fingerprint matches this process and gates are clear, mismatch blocker is absent; supervised can be green."""
    root = tmp_path.resolve()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")
    monkeypatch.delenv("EZRAS_FIRST_20_REQUIRED_FOR_LIVE", raising=False)
    from trading_ai.control.system_execution_lock import save_system_execution_lock
    from trading_ai.orchestration import daemon_live_authority as dla
    from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status

    (root / "data" / "control").mkdir(parents=True)
    (root / "execution_proof").mkdir(parents=True)
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
        runtime_root=root,
    )
    (root / "data/control/go_no_go_decision.json").write_text(
        json.dumps({"ready_for_first_5_trades": True}), encoding="utf-8"
    )
    (root / "data/control/execution_mirror_results.json").write_text(
        json.dumps({"ok": True}), encoding="utf-8"
    )
    (root / "data/control/operator_live_confirmation.json").write_text(
        json.dumps({"confirmed": True}), encoding="utf-8"
    )
    fp = dla.compute_env_fingerprint()
    snap_in = dla.compute_env_fingerprint_inputs()
    (root / "execution_proof" / "live_execution_validation.json").write_text(
        json.dumps(
            {
                "FINAL_EXECUTION_PROVEN": True,
                "execution_success": True,
                "runtime_root": str(root),
                "coinbase_order_verified": True,
                "databank_written": True,
                "supabase_synced": True,
                "governance_logged": True,
                "packet_updated": True,
                "scheduler_stable": True,
                "pnl_calculation_verified": True,
                "partial_failure_codes": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "data/control/daemon_live_switch_authority.json").write_text(
        json.dumps(
            {
                "truth_version": "daemon_live_switch_authority_v1",
                "authoritative_runtime_root": str(root),
                "authoritative_env_fingerprint": fp,
                "fingerprint_inputs_canonical_snapshot": snap_in,
                "avenue_a_can_run_supervised_live_now": True,
                "avenue_a_can_run_autonomous_live_now": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "data/control/daemon_enable_readiness_after_supervised.json").write_text(
        json.dumps({"avenue_a_can_enable_daemon_now": True}), encoding="utf-8"
    )
    (root / "data/control/avenue_a_supervised_live_truth.json").write_text(
        json.dumps({"supervised_live_runtime_proven": True}), encoding="utf-8"
    )

    st = avenue_a_daemon_status(runtime_root=root)
    blockers = st.get("current_supervised_readiness_blockers") or []
    assert not any("runtime_root_or_env_fingerprint_mismatch_vs_daemon_live_switch_authority" in str(b) for b in blockers)
    assert (st.get("runtime_consistency") or {}).get("consistent_with_authoritative_artifacts") is True
    assert (st.get("supervised") or {}).get("can_run_supervised_now") is True
    assert (st.get("supervised") or {}).get("supervised_daemon_enable_ready") is True


def test_avenue_a_daemon_status_writes_daemon_runtime_consistency_truth_matching_stdout_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status recomputes consistency via ``build_daemon_runtime_consistency_truth``; disk JSON matches returned ``runtime_consistency`` keys."""
    root = tmp_path.resolve()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")
    from trading_ai.orchestration import daemon_live_authority as dla
    from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status

    (root / "data" / "control").mkdir(parents=True)
    (root / "execution_proof").mkdir(parents=True)
    fp = dla.compute_env_fingerprint()
    snap_in = dla.compute_env_fingerprint_inputs()
    (root / "data/control/daemon_live_switch_authority.json").write_text(
        json.dumps(
            {
                "truth_version": "daemon_live_switch_authority_v1",
                "authoritative_runtime_root": str(root),
                "authoritative_env_fingerprint": fp,
                "fingerprint_inputs_canonical_snapshot": snap_in,
                "avenue_a_can_run_supervised_live_now": True,
                "avenue_a_can_run_autonomous_live_now": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "execution_proof" / "live_execution_validation.json").write_text(
        json.dumps({"FINAL_EXECUTION_PROVEN": True, "execution_success": True, "runtime_root": str(root)}),
        encoding="utf-8",
    )

    st = avenue_a_daemon_status(runtime_root=root)
    disk = json.loads(
        (root / "data/control/daemon_runtime_consistency_truth.json").read_text(encoding="utf-8")
    )
    rc = st.get("runtime_consistency") or {}
    assert disk.get("consistent_with_authoritative_artifacts") == rc.get("consistent_with_authoritative_artifacts")
    assert disk.get("current_env_fingerprint") == rc.get("current_env_fingerprint")
    assert disk.get("exact_do_not_run_reason_if_inconsistent") == rc.get("exact_do_not_run_reason_if_inconsistent")
