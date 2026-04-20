"""Kill-switch engine, recovery, rehearsals, and execution block integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_consistent(rt: Path) -> None:
    p = rt / "data/control/daemon_runtime_consistency_truth.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"consistent_with_authoritative_artifacts": True, "truth_version": "t"}, indent=2) + "\n",
        encoding="utf-8",
    )
    ad = rt / "data/control/adaptive_live_proof.json"
    ad.write_text(json.dumps({"emergency_brake_triggered": False}, indent=2) + "\n", encoding="utf-8")
    gh = rt / "data/control/gate_b_global_halt_truth.json"
    gh.write_text(json.dumps({"global_halt_is_currently_authoritative": False}, indent=2) + "\n", encoding="utf-8")


def test_activate_halt_blocks_failsafe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_consistent(tmp_path)
    from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks
    from trading_ai.safety.kill_switch_engine import activate_halt

    activate_halt(
        "INVALID_GATE_SELECTION",
        source_component="test",
        severity="HIGH",
        immediate_action_required="test",
        runtime_root=tmp_path,
        rehearsal_mode=True,
    )
    ok, code, msg = run_failsafe_checks(
        FailsafeContext(
            action="place_market_entry",
            avenue_id="coinbase",
            product_id="BTC-USD",
            gate="gate_a",
            quote_notional=10.0,
            base_size=None,
            quote_balances_by_ccy={"USD": 1000.0},
            strategy_id=None,
            trade_id=None,
            multi_leg=False,
            skip_governance=True,
        ),
        runtime_root=tmp_path,
    )
    assert ok is False
    assert "halt_active_reason" in msg or "SYSTEM_KILL_SWITCH" in code


def test_recovery_requires_operator_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_consistent(tmp_path)
    from trading_ai.safety.failsafe_guard import default_failsafe_state, write_failsafe_state
    from trading_ai.safety.kill_switch_engine import activate_halt
    from trading_ai.safety.recovery_engine import attempt_recovery

    st = default_failsafe_state()
    st["halted"] = False
    write_failsafe_state(st, runtime_root=tmp_path)

    activate_halt(
        "RUNTIME_CONSISTENCY_FAILURE",
        source_component="test",
        severity="CRITICAL",
        immediate_action_required="t",
        runtime_root=tmp_path,
        rehearsal_mode=True,
    )
    out = attempt_recovery(runtime_root=tmp_path, operator_confirmed=False, justification="t", rehearsal_mode=True)
    assert out.get("ok") is False


def test_recovery_clears_when_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_consistent(tmp_path)
    from trading_ai.safety.failsafe_guard import default_failsafe_state, write_failsafe_state
    from trading_ai.safety.kill_switch_engine import activate_halt, is_trading_allowed
    from trading_ai.safety.recovery_engine import attempt_recovery

    st = default_failsafe_state()
    st["halted"] = False
    write_failsafe_state(st, runtime_root=tmp_path)

    activate_halt(
        "RUNTIME_CONSISTENCY_FAILURE",
        source_component="test",
        severity="CRITICAL",
        immediate_action_required="t",
        runtime_root=tmp_path,
        rehearsal_mode=True,
    )
    assert is_trading_allowed(runtime_root=tmp_path) is False
    out = attempt_recovery(
        runtime_root=tmp_path,
        operator_confirmed=True,
        resume_mode="supervised",
        justification="test_recovery",
        rehearsal_mode=True,
    )
    assert out.get("ok") is True
    from trading_ai.safety.failsafe_guard import load_kill_switch
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    truth = LocalStorageAdapter(runtime_root=tmp_path).read_json("data/control/kill_switch_truth.json") or {}
    assert truth.get("halted") is False
    assert load_kill_switch(runtime_root=tmp_path) is False
    assert is_trading_allowed(runtime_root=tmp_path) is True


def test_kill_switch_rehearsals_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.safety.kill_switch_rehearsal_runner import run_kill_switch_rehearsals

    summary = run_kill_switch_rehearsals(runtime_root=tmp_path)
    assert summary.get("ok") is True


def test_recovery_rehearsals_pass() -> None:
    from trading_ai.safety.kill_switch_rehearsal_runner import run_recovery_rehearsals

    summary = run_recovery_rehearsals()
    assert summary.get("ok") is True


def test_daemon_abort_sees_engine_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_consistent(tmp_path)
    from trading_ai.safety.kill_switch_engine import activate_halt
    from trading_ai.orchestration.runtime_runner import daemon_abort_conditions

    activate_halt(
        "ENV_FINGERPRINT_MISMATCH",
        source_component="test",
        severity="CRITICAL",
        immediate_action_required="t",
        runtime_root=tmp_path,
        rehearsal_mode=True,
    )
    abort, why, crit = daemon_abort_conditions(runtime_root=tmp_path)
    assert abort is True
    assert crit is True
    assert "halt_active_reason" in why or "kill_switch" in why.lower()
