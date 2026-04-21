"""Regression / drift: window compare and corrective task hints."""

from __future__ import annotations

import os

import pytest

from trading_ai.simulation.regression_drift import compare_recent_vs_baseline


def test_compare_recent_vs_baseline_detects_degrading() -> None:
    series = [10.0, 10.0, 10.0, 10.0, 10.0, 1.0, 1.0, 1.0]
    out = compare_recent_vs_baseline(series, recent_n=3, baseline_n=3, degrade_threshold=5.0, improve_threshold=5.0)
    assert out["verdict"] == "degrading"
    assert out["emit_corrective_tasks"] is True


def test_emit_tasks_when_regression_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")

    from trading_ai.simulation.task_bridge import emit_simulation_tasks, write_sim_tasks_snapshot

    pnl_doc = {"net_total_usd": -10.0, "by_strategy": {}}
    cmp_doc = {"weakest_strategy": "mean_reversion"}
    reg = {"emit_corrective_tasks": True, "verdict": "degrading"}
    rows = emit_simulation_tasks(
        runtime_root=tmp_path,
        pnl_doc=pnl_doc,
        comparisons_doc=cmp_doc,
        regression_doc=reg,
        anomaly_note="unit",
    )
    types = {str(r.get("task_type")) for r in rows}
    assert "regression_drift::sim_corrective" in types
    assert "risk_reduction" in types
    p = write_sim_tasks_snapshot(runtime_root=tmp_path, rows=rows)
    assert p.is_file()
