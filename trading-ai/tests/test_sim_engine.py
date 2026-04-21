"""Simulation engine: lifecycle, PnL rollups, durable artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def sim_rt(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")
    return tmp_path


def test_simulation_tick_writes_artifacts_and_progresses_lessons(sim_rt: Path) -> None:
    from trading_ai.simulation.engine import run_simulation_tick

    for _ in range(24):
        run_simulation_tick(runtime_root=sim_rt)
    ctrl = sim_rt / "data" / "control"
    assert (ctrl / "sim_pnl.json").is_file()
    assert (ctrl / "sim_fill_log.json").is_file()
    assert (ctrl / "sim_trade_log.json").is_file()
    assert (ctrl / "sim_lessons.json").is_file()
    les = json.loads((ctrl / "sim_lessons.json").read_text(encoding="utf-8"))
    assert int(les.get("cycle_seq") or 0) >= 1
    pnl = json.loads((ctrl / "sim_pnl.json").read_text(encoding="utf-8"))
    assert "by_strategy" in pnl
    assert "net_total_usd" in pnl


def test_fill_lifecycle_terminals(sim_rt: Path) -> None:
    from trading_ai.simulation.fill_lifecycle import advance_simulated_fill_once

    seen = set()
    for i in range(80):
        out = advance_simulated_fill_once(runtime_root=sim_rt, tick_index=i)
        seen.add(str(out.get("phase")))
    assert "filled" in seen or "canceled" in seen or "rejected" in seen
