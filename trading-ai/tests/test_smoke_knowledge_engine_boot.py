import pytest

from trading_ai.global_layer.data_knowledge_engine import DataKnowledgeEngine
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore


def test_knowledge_engine_boots(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    out = DataKnowledgeEngine(store).run_once()
    assert "learned" in out
    assert "synthesis" in out
