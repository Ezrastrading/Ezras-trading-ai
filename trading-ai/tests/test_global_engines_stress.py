"""Stress scenarios for speed progression + knowledge engines (deterministic, no network)."""

from __future__ import annotations

import pytest

from trading_ai.global_layer.data_knowledge_engine import DataKnowledgeEngine
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine
from trading_ai.global_layer.knowledge_synthesizer import synthesize
from trading_ai.nte.capital_ledger import load_ledger, save_ledger


def test_speed_engine_survives_missing_supabase(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    store = GlobalMemoryStore()
    store.ensure_all()
    out = SpeedProgressionEngine(store).run_once()
    assert "supabase_notes" in out
    assert any("supabase" in str(x).lower() for x in (out.get("supabase_notes") or []))


def test_deposits_separate_from_profit_in_internal_read(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    led = load_ledger()
    led["starting_capital"] = 100.0
    led["capital_added"] = 1000.0
    led["realized_pnl_net"] = 50.0
    led["deposits_usd"] = 1000.0
    led["realized_pnl_usd"] = 50.0
    save_ledger(led)
    from trading_ai.global_layer.internal_data_reader import read_normalized_internal

    internal = read_normalized_internal()
    cl = internal["capital_ledger"]
    assert float(cl.get("capital_added_usd") or 0) == 1000.0
    assert float(cl.get("realized_pnl_usd") or 0) == 50.0


def test_one_avenue_only_mode_speed_still_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_ACTIVE_AVENUES", "coinbase")
    store = GlobalMemoryStore()
    store.ensure_all()
    out = SpeedProgressionEngine(store).run_once()
    assert out.get("strongest_avenue") is not None or out.get("current_status")


def test_weak_external_vs_strong_internal_synthesis(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    internal = {
        "trades": [{"net_pnl_usd": 1.0} for _ in range(25)],
        "capital_ledger": {"net_equity_estimate_usd": 5000},
    }
    ext = [
        {
            "title": "random blog edge",
            "summary": "buy high sell higher",
            "overall_rank": 0.2,
            "source_id": "ext1",
            "avenue_relevance": "global",
        }
    ]
    syn = synthesize(internal=internal, external_candidates=ext, store=store)
    assert "knowledge_base" in syn
    kb = store.load_json("knowledge_base.json")
    assert kb.get("global_truths")


def test_thin_internal_vs_strong_external_ranked(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    internal = {"trades": [], "capital_ledger": {}}
    ext = [
        {
            "title": "quant paper",
            "summary": "microstructure",
            "overall_rank": 0.95,
            "source_id": "ext2",
            "avenue_relevance": "coinbase",
        }
    ]
    synthesize(internal=internal, external_candidates=ext, store=store)
    si = store.load_json("strategy_intelligence.json")
    assert si.get("strategy_families")


def test_data_knowledge_engine_run_once_no_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    store = GlobalMemoryStore()
    store.ensure_all()
    out = DataKnowledgeEngine(store).run_once()
    assert "learned" in out or "synthesis" in out
