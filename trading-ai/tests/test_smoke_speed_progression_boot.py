import json
import time

import pytest

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine


def test_speed_progression_engine_boots(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    eng = SpeedProgressionEngine(store)
    out = eng.run_once()
    assert out["schema_version"] == "1.0"
    assert out["active_goal"] in ("A", "B", "C", "POST_C")
    assert "acceleration_options" in out


def test_speed_progression_writes_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    SpeedProgressionEngine(GlobalMemoryStore()).run_once()
    p = tmp_path / "shark" / "memory" / "global" / "speed_progression.json"
    assert p.is_file()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw.get("active_goal")
