"""final_system_lock_status must not mark Gate A live-proven without real execution_proof booleans."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.reports.gate_parity_reports import write_final_system_lock_status


def test_gate_a_not_proven_without_execution_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = write_final_system_lock_status(runtime_root=tmp_path)
    assert out["gate_a"]["live_micro_proven"] is False
    assert any("missing" in x for x in out["gate_a"]["blockers"])


def test_gate_a_proven_only_with_final_execution_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ep = tmp_path / "execution_proof"
    ep.mkdir(parents=True)
    (ep / "live_execution_validation.json").write_text(
        json.dumps(
            {
                "FINAL_EXECUTION_PROVEN": True,
                "coinbase_order_verified": True,
                "READY_FOR_FIRST_20": True,
            }
        ),
        encoding="utf-8",
    )
    out = write_final_system_lock_status(runtime_root=tmp_path)
    assert out["gate_a"]["live_micro_proven"] is True
    assert out["gate_a"]["blockers"] == []


def test_gate_b_staged_distinct_from_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "gate_b_validation.json").write_text(
        json.dumps(
            {
                "validated_at": "2026-01-01T00:00:00+00:00",
                "micro_validation_pass": True,
                "failed_validation": False,
                "validation_mode": "staged_mock_no_venue_orders",
            }
        ),
        encoding="utf-8",
    )
    out = write_final_system_lock_status(runtime_root=tmp_path)
    assert out["gate_b"]["staged_micro_ready"] is True
    assert out["gate_b"]["live_micro_ready"] is False


def test_gate_b_live_micro_proven_from_execution_proof_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ep = tmp_path / "execution_proof"
    ep.mkdir(parents=True)
    (ep / "gate_b_live_execution_validation.json").write_text(
        json.dumps(
            {
                "FINAL_EXECUTION_PROVEN": True,
                "gate_b_order_verified": True,
            }
        ),
        encoding="utf-8",
    )
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "gate_b_validation.json").write_text(
        json.dumps(
            {
                "validated_at": "2026-01-01T00:00:00+00:00",
                "micro_validation_pass": True,
                "failed_validation": False,
                "validation_mode": "staged_mock_no_venue_orders",
            }
        ),
        encoding="utf-8",
    )
    out = write_final_system_lock_status(runtime_root=tmp_path)
    assert out["gate_b"]["live_micro_proven"] is True
    assert out["gate_b"]["live_micro_ready"] is True
