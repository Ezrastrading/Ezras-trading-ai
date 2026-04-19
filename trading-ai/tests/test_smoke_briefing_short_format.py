import pytest

from trading_ai.global_layer.briefing_engine import BriefingEngine
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine


def test_briefing_stays_concise(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    SpeedProgressionEngine(store).run_once()
    out = BriefingEngine(store).run_once(touch_research_memory=False)
    text = out["text"]
    assert len(text) < 6000
    assert "Top 3 actions" in text
    assert "Top 3 risks" in text
