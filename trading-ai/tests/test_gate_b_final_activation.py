"""Final Gate B activation bundle — decision audit, gaps, blockers vs sequence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.reports.gate_b_final_activation import (
    build_gate_b_final_decision_audit,
    write_gate_b_final_activation_artifacts,
)
from trading_ai.reports.gate_b_control_truth import write_gate_b_truth_artifacts
from trading_ai.reports.gate_b_final_go_live_truth import write_gate_b_final_go_live_truth
from trading_ai.reports.gate_b_loop_truth import write_gate_b_loop_truth_artifacts
from trading_ai.reports.lessons_runtime_truth import write_lessons_runtime_truth_artifacts


def test_final_activation_writes_blockers_when_not_switchable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    write_gate_b_truth_artifacts(runtime_root=tmp_path)
    write_lessons_runtime_truth_artifacts(runtime_root=tmp_path)
    write_gate_b_loop_truth_artifacts(runtime_root=tmp_path)
    write_gate_b_final_go_live_truth(runtime_root=tmp_path)
    out = write_gate_b_final_activation_artifacts(runtime_root=tmp_path)
    ctrl = tmp_path / "data" / "control"
    assert (ctrl / "gate_b_final_decision_audit.json").is_file()
    assert (ctrl / "gate_b_remaining_gaps_final.json").is_file()
    fin = json.loads((ctrl / "gate_b_final_go_live_truth.json").read_text(encoding="utf-8"))
    if fin.get("gate_b_can_be_switched_live_now"):
        assert (ctrl / "gate_b_safe_activation_sequence.json").is_file()
        assert not (ctrl / "gate_b_activation_blockers.json").is_file()
    else:
        assert (ctrl / "gate_b_activation_blockers.json").is_file()
        assert not (ctrl / "gate_b_safe_activation_sequence.json").is_file()
    assert out["can_switch"] == bool(fin.get("gate_b_can_be_switched_live_now"))


def test_decision_audit_questions_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    write_gate_b_truth_artifacts(runtime_root=tmp_path)
    write_lessons_runtime_truth_artifacts(runtime_root=tmp_path)
    write_gate_b_loop_truth_artifacts(runtime_root=tmp_path)
    write_gate_b_final_go_live_truth(runtime_root=tmp_path)
    audit = build_gate_b_final_decision_audit(runtime_root=tmp_path)
    assert "Q1_can_gate_b_place_live_coinbase_orders_now" in audit
    assert audit["Q3_can_gate_b_run_continuously_24_7_in_repo_daemon_now"]["answer"] is False
    assert audit["Q4_are_lessons_actively_affecting_gate_b_trading_decisions_now"]["answer"] is False
