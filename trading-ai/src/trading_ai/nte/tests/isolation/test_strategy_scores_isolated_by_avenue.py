"""Editing one avenue block must not mutate another."""

from __future__ import annotations


def test_coinbase_scores_change_does_not_mutate_kalshi_block(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    st = MemoryStore()
    st.ensure_defaults()
    ss = st.load_json("strategy_scores.json")
    ss.setdefault("avenues", {})["coinbase"] = {"mean_reversion": {"score": 0.7}}
    ss.setdefault("avenues", {})["kalshi"] = {"mean_reversion": {"score": 0.3}}
    st.save_json("strategy_scores.json", ss)

    ss3 = st.load_json("strategy_scores.json")
    ss3["avenues"]["coinbase"]["mean_reversion"]["score"] = 0.99
    st.save_json("strategy_scores.json", ss3)

    final = st.load_json("strategy_scores.json")
    assert final["avenues"]["kalshi"]["mean_reversion"]["score"] == 0.3
