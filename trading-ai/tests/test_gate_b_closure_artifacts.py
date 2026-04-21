"""Gate B operator truth artifacts — adaptive scope fields, lessons honesty, loop/final bundles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.control.adaptive_scope import audit_trade_event_row_stats, row_trading_gate, strategy_id_excluded_from_production_adaptive
from trading_ai.reports.gate_b_control_truth import write_gate_b_truth_artifacts
from trading_ai.reports.gate_b_final_go_live_truth import build_gate_b_final_go_live_truth, write_gate_b_final_go_live_truth
from trading_ai.reports.gate_b_loop_truth import build_gate_b_loop_truth, write_gate_b_loop_truth_artifacts
from trading_ai.reports.lessons_runtime_truth import build_lessons_runtime_truth, write_lessons_runtime_truth_artifacts


def _write_trade_events(tmp_path: Path, rows: list) -> None:
    db = tmp_path / "databank"
    db.mkdir(parents=True)
    p = db / "trade_events.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_audit_trade_event_row_stats_validation_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_trade_events(
        tmp_path,
        [
            {"trade_id": "v1", "net_pnl": -1.0, "trading_gate": "gate_b", "strategy_id": "gate_b_live_micro_validation"},
            {"trade_id": "p1", "net_pnl": 2.0, "trading_gate": "gate_b", "strategy_id": "prod"},
            {"trade_id": "a1", "net_pnl": -3.0, "trading_gate": "gate_a", "strategy_id": "prod_a"},
        ],
    )
    st = audit_trade_event_row_stats(production_only=True)
    assert st["validation_or_nonproduction_rows_excluded"] >= 1
    assert st["gate_b_rows_seen_count"] == 1
    assert st["gate_a_rows_seen_count"] == 1
    assert strategy_id_excluded_from_production_adaptive("gate_b_live_micro_validation")


def test_gate_b_truth_artifacts_row_stats_and_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("GATE_B_LIVE_EXECUTION_ENABLED", raising=False)
    _write_trade_events(
        tmp_path,
        [
            {"trade_id": "p1", "net_pnl": 1.0, "trading_gate": "gate_b", "strategy_id": "x"},
        ],
    )
    out = write_gate_b_truth_artifacts(runtime_root=tmp_path)
    ctrl = tmp_path / "data" / "control"
    ca = json.loads((ctrl / "gate_b_scope_contamination_audit.json").read_text(encoding="utf-8"))
    assert ca.get("evaluation_scope_used") == "gate_b"
    assert "validation_rows_excluded_count" in ca or "validation_or_nonproduction_rows_excluded" in ca
    at = json.loads((ctrl / "gate_b_adaptive_truth.json").read_text(encoding="utf-8"))
    assert at.get("evaluation_scope_used") == "gate_b"
    assert at.get("row_stats", {}).get("gate_b_rows_seen_count") == 1
    assert "gate_b_live_status.json" in out["paths"]


def test_lessons_runtime_truth_gate_b_not_using_lessons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True, exist_ok=True)
    p = build_lessons_runtime_truth(runtime_root=tmp_path)
    assert p["lessons_influence_candidate_ranking"] is False
    assert p["runtime_reads_lessons"] is False
    write_lessons_runtime_truth_artifacts(runtime_root=tmp_path)
    eff = json.loads((tmp_path / "data" / "control" / "lessons_effect_on_runtime.json").read_text(encoding="utf-8"))
    assert eff["runtime_influences_decisions"]["gate_b_coinbase"] is False


def test_gate_b_loop_truth_no_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True, exist_ok=True)
    lt = build_gate_b_loop_truth(runtime_root=tmp_path)
    assert lt["dedicated_gate_b_scheduler_exists"] is False
    assert lt["production_loop_proven"] is False
    write_gate_b_loop_truth_artifacts(runtime_root=tmp_path)
    assert (tmp_path / "data" / "control" / "gate_b_runner_contract.json").is_file()


def test_gate_b_loop_truth_after_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "gate_b_last_production_tick.json").write_text(
        json.dumps({"tick_ok": True, "generated_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    lt = build_gate_b_loop_truth(runtime_root=tmp_path)
    assert lt["production_loop_proven"] is True


def test_gate_b_final_truth_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    write_gate_b_truth_artifacts(runtime_root=tmp_path)
    write_lessons_runtime_truth_artifacts(runtime_root=tmp_path)
    write_gate_b_loop_truth_artifacts(runtime_root=tmp_path)
    write_gate_b_final_go_live_truth(runtime_root=tmp_path)
    fin = json.loads((tmp_path / "data" / "control" / "gate_b_final_go_live_truth.json").read_text(encoding="utf-8"))
    assert fin["truth_version"] == "gate_b_final_go_live_truth_v2"
    assert "gate_b_can_be_switched_live_now" in fin


def test_row_trading_gate_prefers_trading_gate() -> None:
    assert row_trading_gate({"trading_gate": "gate_b", "gate_id": "gate_a"}) == "gate_b"


def test_no_gate_a_in_gate_b_scope_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.control.adaptive_scope import filter_events_for_scope, load_trade_events_for_adaptive

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_trade_events(
        tmp_path,
        [{"trade_id": "a", "net_pnl": -5.0, "trading_gate": "gate_a", "strategy_id": "p"}],
    )
    raw = load_trade_events_for_adaptive()
    gb = filter_events_for_scope(raw, scope="gate_b", production_only=True)
    assert gb == []
