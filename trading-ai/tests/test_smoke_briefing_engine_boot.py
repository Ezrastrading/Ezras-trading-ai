import pytest

from trading_ai.global_layer.briefing_engine import BriefingEngine
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine


def test_briefing_engine_boots(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    SpeedProgressionEngine(store).run_once()
    out = BriefingEngine(store).run_once(touch_research_memory=False)
    assert "text" in out
    assert len(out.get("top_3_actions", [])) == 3
