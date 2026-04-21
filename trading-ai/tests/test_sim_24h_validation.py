"""Strict sim validation JSON."""

from __future__ import annotations

import json
import os

import pytest


def test_sim_24h_validation_with_seeded_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")

    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    trades = [{"status": "closed", "net_pnl_usd": float(i % 3 - 1), "simulated_non_live": True} for i in range(120)]
    (ctrl / "sim_trade_log.json").write_text(
        json.dumps({"truth_version": "sim_trade_log_v1", "count": len(trades), "trades": trades}),
        encoding="utf-8",
    )
    lessons = {
        "truth_version": "sim_lessons_v1",
        "cycle_seq": 20,
        "lessons": [{"t": "x", "trade_cycle": True}] * 10,
    }
    (ctrl / "sim_lessons.json").write_text(json.dumps(lessons), encoding="utf-8")

    gov = tmp_path / "data" / "governance" / "global_layer"
    gov.mkdir(parents=True, exist_ok=True)
    rows = [
        {"task_type": "comparisons::avenue", "task_id": "a"},
        {"task_type": "risk_reduction", "task_id": "b"},
        {"task_type": "mission_goals::research", "task_id": "c"},
        {"task_type": "regression::investigate", "task_id": "d"},
    ]
    with (gov / "tasks.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    from trading_ai.simulation.validation import write_sim_24h_validation

    out = write_sim_24h_validation(
        runtime_root=tmp_path,
        min_simulated_trades=100,
        min_supervisor_cycles=8,
        ticks_executed=50,
    )
    assert out.get("ok") is True
    assert (ctrl / "sim_24h_validation.json").is_file()
