"""Global halt decoupling for Gate B switch-live — informational brake only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.reports.gate_b_global_halt_truth import (
    build_gate_b_global_halt_truth,
    compute_gate_b_can_be_switched_live_now,
)


def test_global_halt_truth_runs_without_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True, exist_ok=True)
    out = build_gate_b_global_halt_truth(runtime_root=tmp_path)
    assert "global_halt_primary_classification" in out
    assert "fresh_brake_global" in out


def test_compute_switch_when_no_global_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ok, _gh = compute_gate_b_can_be_switched_live_now(
        runtime_root=tmp_path,
        micro_live=True,
        ready_orders=True,
        blocked_gb_adaptive=False,
        blocked_global_adaptive_raw=False,
    )
    assert ok is True
