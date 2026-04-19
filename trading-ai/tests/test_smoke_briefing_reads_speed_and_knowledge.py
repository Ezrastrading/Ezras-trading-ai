import pytest

from trading_ai.global_layer.briefing_engine import BriefingEngine
from trading_ai.global_layer.data_knowledge_engine import DataKnowledgeEngine
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine


def test_briefing_references_internal_and_external_layers(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    SpeedProgressionEngine(store).run_once()
    DataKnowledgeEngine(store).run_once()
    out = BriefingEngine(store).run_once(touch_research_memory=False)
    assert "Internal" in out["text"] or "**A — Internal**" in out["text"]
    assert "External" in out["text"] or "**B — External" in out["text"]
