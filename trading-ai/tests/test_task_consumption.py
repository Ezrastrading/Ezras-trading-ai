"""Simulation task emission carries avenue, gate, bot scope."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_sim_tasks_include_scope(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")

    plan = tmp_path / "data" / "control" / "mission_goals_operating_plan.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        json.dumps(
            {
                "pace": {"pace_state": "behind_pace"},
                "active_goal": {"id": "GOAL_B"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    from trading_ai.simulation.task_bridge import emit_simulation_tasks

    rows = emit_simulation_tasks(
        runtime_root=tmp_path,
        pnl_doc={"net_total_usd": -5.0, "by_strategy": {"a": {"net_usd": -5.0}}},
        comparisons_doc={"weakest_strategy": "x"},
        regression_doc=None,
    )
    assert rows
    for r in rows:
        sc = r.get("scope")
        assert isinstance(sc, dict)
        assert "avenue" in sc and "gate" in sc and "bot" in sc
        assert "mission_influence" in r
