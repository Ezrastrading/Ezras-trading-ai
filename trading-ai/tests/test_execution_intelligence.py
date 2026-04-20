"""Execution Intelligence Engine — goals, state, progress, plans (advisory)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from trading_ai.intelligence.execution_intelligence.evaluation import (
    attach_raw_trades,
    evaluate_goal_progress,
    infer_operating_mode,
)
from trading_ai.intelligence.execution_intelligence.goals import GOAL_A, GOAL_B, get_goal
from trading_ai.intelligence.execution_intelligence.persistence import refresh_execution_intelligence, select_active_goal
from trading_ai.intelligence.execution_intelligence.system_state import get_system_state
from trading_ai.nte.memory.store import MemoryStore


def _write_minimal_ledger(tmp_path: Path, realized: float) -> None:
    p = tmp_path / "shark" / "nte" / "memory" / "capital_ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "starting_capital": 100.0,
                "capital_added": 0.0,
                "withdrawals": 0.0,
                "realized_pnl_net": realized,
                "realized_pnl_usd": realized,
                "unrealized_pnl": 0.0,
                "rolling_7d_net_profit": 0.0,
                "rolling_30d_net_profit": 0.0,
                "entries": [],
            }
        ),
        encoding="utf-8",
    )


def test_select_active_goal_detects_goal_a(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_minimal_ledger(tmp_path, 100.0)
    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = []
    ms.save_json("trade_memory.json", tm)
    st = get_system_state(store=ms)
    assert select_active_goal(st, [], now_ts=time.time()) == GOAL_A


def test_goal_a_progress_not_inflated(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_minimal_ledger(tmp_path, 250.0)
    ms = MemoryStore()
    ms.ensure_defaults()
    g = get_goal(GOAL_A) or {}
    st = attach_raw_trades(get_system_state(store=ms), [])
    ev = evaluate_goal_progress(g, st)
    assert ev["progress_pct"] == pytest.approx(25.0, rel=0.01)
    assert ev["trajectory_status"] == "behind"


def test_safe_plan_includes_constraints(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_minimal_ledger(tmp_path, 0.0)
    ms = MemoryStore()
    ms.ensure_defaults()
    out = refresh_execution_intelligence(ms, persist=False)
    plan = out["daily_plan"]
    txt = " ".join(plan.get("execution_constraints") or [])
    assert "max risk" in txt.lower() or "risk" in txt.lower()
    assert "stop" in txt.lower()
    assert plan.get("disclaimer")


def test_fallback_when_trade_memory_missing_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_minimal_ledger(tmp_path, 0.0)
    ms = MemoryStore()
    ms.ensure_defaults()
    st = get_system_state(store=ms)
    assert st["trade_count_today"] == 0
    assert st["win_rate"] is None


def test_stabilization_mode_when_weak(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_minimal_ledger(tmp_path, 0.0)
    ms = MemoryStore()
    ms.ensure_defaults()
    now = time.time()
    trades = []
    for i in range(12):
        trades.append(
            {
                "trade_id": f"t{i}",
                "net_pnl_usd": -5.0,
                "logged_at": now - i * 3600,
                "avenue": "coinbase",
                "setup_type": "mean_reversion",
            }
        )
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = trades
    ms.save_json("trade_memory.json", tm)
    st = get_system_state(store=ms)
    assert infer_operating_mode(st) in ("stabilization", "capital_protection")


def test_goal_b_weekly_evaluation(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_minimal_ledger(tmp_path, 5000.0)
    ms = MemoryStore()
    ms.ensure_defaults()
    now = time.time()
    # Two trades in "current" ISO week with high net — still need goal_a met first for select; use ledger 5000 so A met
    trades = [
        {
            "trade_id": "a1",
            "net_pnl_usd": 600.0,
            "logged_at": now,
            "avenue": "coinbase",
        },
        {
            "trade_id": "a2",
            "net_pnl_usd": 500.0,
            "logged_at": now - 100,
            "avenue": "coinbase",
        },
    ]
    g = get_goal(GOAL_B) or {}
    st = attach_raw_trades(get_system_state(store=ms, now_ts=now), trades)
    ev = evaluate_goal_progress(g, st)
    assert "progress_pct" in ev
    assert ev["trajectory_status"] in ("ahead", "on_track", "behind")
