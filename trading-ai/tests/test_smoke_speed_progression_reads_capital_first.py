import json
import time
from pathlib import Path

import pytest

from trading_ai.global_layer.internal_data_reader import read_normalized_internal
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine
from trading_ai.nte.paths import nte_capital_ledger_path, nte_memory_dir


def test_internal_reader_key_order_capital_first(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    read_normalized_internal()  # ensure files
    keys = list(read_normalized_internal().keys())
    assert keys[0] == "capital_ledger"


def test_deposit_blocker_not_counted_as_realized_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    nte_memory_dir().mkdir(parents=True, exist_ok=True)
    ledger = {
        "schema_version": 1,
        "starting_capital_usd": 100.0,
        "deposits_usd": 500.0,
        "withdrawals_usd": 0.0,
        "realized_pnl_usd": 0.0,
        "unrealized_pnl_usd": 0.0,
        "entries": [],
    }
    Path(nte_capital_ledger_path()).write_text(json.dumps(ledger), encoding="utf-8")
    tm_path = nte_memory_dir() / "trade_memory.json"
    tm_path.write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "net_pnl_usd": 10.0,
                        "logged_at": time.time(),
                        "avenue": "coinbase",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out = SpeedProgressionEngine().run_once()
    names = [b["name"] for b in out.get("blockers", [])]
    assert "low_realized_vs_deposits" in names
