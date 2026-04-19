import json
import time
from pathlib import Path

import pytest

from trading_ai.global_layer.goal_evolution_engine import propose_post_c_goals
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine
from trading_ai.nte.paths import nte_capital_ledger_path, nte_memory_dir


def test_post_c_goals_empty_when_below_c(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    assert propose_post_c_goals(rolling_7d=100.0, rolling_30d=100.0, avenue_mix={}) == []


def test_speed_engine_generates_post_c_when_metrics_hit(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    nte_memory_dir().mkdir(parents=True, exist_ok=True)
    ledger = {
        "schema_version": 1,
        "starting_capital_usd": 2000.0,
        "deposits_usd": 0.0,
        "withdrawals_usd": 0.0,
        "realized_pnl_usd": 500.0,
        "unrealized_pnl_usd": 0.0,
        "entries": [],
    }
    Path(nte_capital_ledger_path()).write_text(json.dumps(ledger), encoding="utf-8")
    now = time.time()
    trades = []
    for i in range(6):
        trades.append(
            {
                "net_pnl_usd": 400.0,
                "logged_at": now - i * 3600,
                "avenue": "coinbase",
            }
        )
    tm_path = nte_memory_dir() / "trade_memory.json"
    tm_path.write_text(json.dumps({"trades": trades}), encoding="utf-8")

    out = SpeedProgressionEngine().run_once()
    assert out["active_goal"] == "POST_C"
    gg_path = tmp_path / "shark" / "memory" / "global" / "generated_goals.json"
    gg = json.loads(gg_path.read_text(encoding="utf-8"))
    assert len(gg.get("post_goal_c_candidates", [])) >= 1
