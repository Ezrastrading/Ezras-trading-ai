"""Adaptive OS must not halt Gate B on Gate A-only production history (scoped PnL)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.control.adaptive_scope import (
    build_scoped_trade_history,
    filter_events_for_scope,
    load_trade_events_for_adaptive,
    row_counts_for_production_adaptive_pnl,
)
from trading_ai.control.live_adaptive_integration import build_live_operating_snapshot


def _write_events(tmp_path: Path, rows: list) -> None:
    db = tmp_path / "databank"
    db.mkdir(parents=True)
    p = db / "trade_events.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    import os

    os.environ["EZRAS_RUNTIME_ROOT"] = str(tmp_path)


def test_gate_a_losses_excluded_from_gate_b_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rows = [
        {
            "trade_id": "t1",
            "net_pnl": -50.0,
            "trading_gate": "gate_a",
            "strategy_id": "prod_a",
        },
        {
            "trade_id": "t2",
            "net_pnl": -50.0,
            "trading_gate": "gate_a",
            "strategy_id": "prod_a",
        },
    ]
    _write_events(tmp_path, rows)
    gb_pnls, meta = build_scoped_trade_history(scope="gate_b", production_only=True, max_n=80)
    assert gb_pnls == []
    assert meta["scoped_row_count"] == 0


def test_validation_strategy_ids_excluded_from_production_series(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rows = [
        {
            "trade_id": "v1",
            "net_pnl": -100.0,
            "trading_gate": "gate_b",
            "strategy_id": "gate_b_live_micro_validation",
        },
        {
            "trade_id": "p1",
            "net_pnl": 2.0,
            "trading_gate": "gate_b",
            "strategy_id": "gate_b_prod",
        },
    ]
    _write_events(tmp_path, rows)
    raw = load_trade_events_for_adaptive()
    filt = filter_events_for_scope(raw, scope="gate_b", production_only=True)
    assert len(filt) == 1
    assert filt[0]["trade_id"] == "p1"


def test_build_snapshot_gate_b_empty_pnls_not_gate_a_contaminated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rows = [
        {"trade_id": "a1", "net_pnl": -10.0, "trading_gate": "gate_a", "strategy_id": "x"},
    ] * 6
    _write_events(tmp_path, rows)
    snap = build_live_operating_snapshot(evaluation_scope="gate_b", production_pnl_only=True)
    assert snap.last_n_trade_pnls == []
    assert snap.consecutive_losses == 0


def test_row_counts_for_production_adaptive_pnl() -> None:
    assert row_counts_for_production_adaptive_pnl(
        {"strategy_id": "live_execution_validation", "net_pnl": -1},
        production_only=True,
    ) is False
    assert row_counts_for_production_adaptive_pnl(
        {"strategy_id": "live_execution_validation", "net_pnl": -1},
        production_only=False,
    ) is True


def test_write_gate_b_truth_artifacts_writes_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.reports.gate_b_control_truth import write_gate_b_truth_artifacts

    out = write_gate_b_truth_artifacts(runtime_root=tmp_path)
    assert "gate_b_adaptive_truth.json" in (out.get("paths") or {})
    assert (tmp_path / "data" / "control" / "gate_b_adaptive_truth.json").is_file()
    assert (tmp_path / "data" / "reports" / "gate_b_daily_operator_report.json").is_file()
