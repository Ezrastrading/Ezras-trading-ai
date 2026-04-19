import json
from pathlib import Path

import pytest

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.knowledge_synthesizer import synthesize


def test_low_rank_external_recorded_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ext_path = tmp_path / "ext.json"
    ext_path.write_text(
        json.dumps(
            [
                {
                    "source_type": "other",
                    "title": "guaranteed alpha no fees",
                    "summary": "fluff",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GLOBAL_EXTERNAL_SOURCES_PATH", str(ext_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    from trading_ai.global_layer.external_source_reader import read_external_candidates

    cands = read_external_candidates()["candidates"]
    synthesize(internal={"trades": [], "capital_ledger": {}}, external_candidates=cands, store=store)
    rej = store.load_json("rejected_strategy_ideas.json")
    titles = [r.get("title") for r in (rej.get("rejected") or [])]
    assert any("guaranteed" in (t or "").lower() for t in titles)
