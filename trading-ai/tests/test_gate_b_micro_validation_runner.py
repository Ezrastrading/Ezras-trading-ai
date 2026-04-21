"""Gate B staged micro-validation runner (no venue orders)."""

from __future__ import annotations

import json

import pytest

from trading_ai.prelive import gate_b_staged_validation
from trading_ai.prelive.gate_b_micro_validation_runner import run as run_gate_b_micro
from trading_ai.reports.gate_parity_reports import write_final_system_lock_status


def test_gate_b_micro_runner_writes_validation_and_proof(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    proof = run_gate_b_micro(runtime_root=tmp_path, write_ledger=True)
    assert proof.get("validation_kind") == "gate_b_staged_micro"
    ctrl = tmp_path / "data" / "control"
    assert (ctrl / "gate_b_validation.json").is_file()
    assert (ctrl / "gate_b_micro_validation_proof.json").is_file()
    raw = json.loads((ctrl / "gate_b_validation.json").read_text(encoding="utf-8"))
    assert raw.get("micro_validation_pass") is True
    assert raw.get("validation_mode") == "staged_mock_no_venue_orders"
    dup = proof.get("duplicate_trade_guard") or {}
    assert dup.get("passed") is True


def test_staged_validation_invokes_micro_and_writes_final_lock(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("GATE_B_LIVE_EXECUTION_ENABLED", raising=False)
    out = gate_b_staged_validation.run(runtime_root=tmp_path)
    assert out.get("gate_b_micro_validation_proof_summary", {}).get("all_passed") is True
    fl = write_final_system_lock_status(runtime_root=tmp_path)
    assert fl["gate_b"]["staged_micro_ready"] is True
    assert fl["gate_a"]["live_micro_proven"] is False
    assert (tmp_path / "data" / "control" / "final_system_lock_status.json").is_file()


def test_readiness_micro_without_operator_enable(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("GATE_B_LIVE_EXECUTION_ENABLED", raising=False)
    run_gate_b_micro(runtime_root=tmp_path, write_ledger=False)
    from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report

    r = gate_b_live_status_report()
    assert r.get("readiness_state") == "micro_validated"
