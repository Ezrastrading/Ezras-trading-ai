import pytest

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.knowledge_synthesizer import synthesize


def test_internal_truth_recorded_before_external(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    internal = {
        "trades": [{"net_pnl_usd": 5.0} for _ in range(15)],
        "capital_ledger": {},
    }
    ext = [
        {
            "source_id": "x",
            "source_type": "other",
            "title": "noise",
            "summary": "vague alpha",
            "url": "",
            "avenue_relevance": "global",
            "strategy_family": "unknown",
            "execution_relevance": "low",
            "credibility_indicators": [],
            "testability": "low",
        }
    ]
    synthesize(internal=internal, external_candidates=ext, store=store)
    kb = store.load_json("knowledge_base.json")
    truths = kb.get("global_truths") or []
    assert any("Internal net across logged trades" in t for t in truths)
