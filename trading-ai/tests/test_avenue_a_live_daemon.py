"""Avenue A daemon policy + rebuy gating — no live orders without explicit test harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_daemon_mode_default_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EZRAS_AVENUE_A_DAEMON_MODE", raising=False)
    from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_daemon_mode

    assert avenue_a_daemon_mode() == "disabled"


def test_autonomous_unpinned_does_not_default_to_btc_usd() -> None:
    from trading_ai.orchestration.avenue_a_live_daemon import _effective_daemon_product_id

    assert _effective_daemon_product_id(mode="autonomous_live", product_id=None) is None
    assert _effective_daemon_product_id(mode="autonomous_live", product_id="AUTO") is None


def test_supervised_blocked_without_operator_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "control" / "system_execution_lock.json").write_text(
        json.dumps(
            {
                "system_locked": True,
                "ready_for_live_execution": True,
                "gate_a_enabled": True,
                "gate_b_enabled": False,
                "safety_checks": {
                    "policy_aligned": True,
                    "capital_truth_valid": True,
                    "artifacts_writing": True,
                    "supabase_connected": True,
                },
            }
        ),
        encoding="utf-8",
    )
    from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_supervised_runtime_allowed

    ok, why = avenue_a_supervised_runtime_allowed(runtime_root=tmp_path)
    assert ok is False
    assert "operator" in why.lower() or "confirmation" in why.lower()


def test_autonomous_runtime_proven_false_without_verification_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_autonomous_runtime_proven

    ok, why = avenue_a_autonomous_runtime_proven(runtime_root=tmp_path)
    assert ok is False
    assert "verification" in why.lower()


def test_rebuy_blocked_when_loop_proof_not_finalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "universal_execution_loop_proof.json").write_text(
        json.dumps(
            {
                "final_execution_proven": False,
                "lifecycle_stages": {
                    "entry_fill_confirmed": True,
                    "exit_fill_confirmed": False,
                },
            }
        ),
        encoding="utf-8",
    )
    from trading_ai.orchestration.avenue_a_live_daemon import _rebuy_allows_next_entry

    ok, why = _rebuy_allows_next_entry(tmp_path)
    assert ok is False


def test_gate_a_confirm_requires_daemon_active_for_ack_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EZRAS_AVENUE_A_DAEMON_ACTIVE", raising=False)
    monkeypatch.delenv("LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM", raising=False)
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "control" / "avenue_a_autonomous_live_ack.json").write_text(
        json.dumps({"confirmed": True, "scope": "avenue_a_repeated_gate_a_cycles"}),
        encoding="utf-8",
    )
    from trading_ai.runtime_proof.live_execution_validation import _gate_a_operator_confirms_live_round_trip

    assert _gate_a_operator_confirms_live_round_trip(tmp_path) is False


def test_gate_a_confirm_with_daemon_active_and_ack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_ACTIVE", "1")
    monkeypatch.delenv("LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM", raising=False)
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "control" / "avenue_a_autonomous_live_ack.json").write_text(
        json.dumps({"confirmed": True, "scope": "avenue_a_repeated_gate_a_cycles"}),
        encoding="utf-8",
    )
    from trading_ai.runtime_proof.live_execution_validation import _gate_a_operator_confirms_live_round_trip

    assert _gate_a_operator_confirms_live_round_trip(tmp_path) is True


def test_emit_gate_a_universal_skips_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.universal_execution.gate_b_proof_bridge import try_emit_universal_loop_proof_from_gate_a_file

    out = try_emit_universal_loop_proof_from_gate_a_file(runtime_root=tmp_path)
    assert out.get("emitted") is False
