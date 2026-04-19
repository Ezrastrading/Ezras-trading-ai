"""Reward state is avenue-scoped in strategy_scores; global reward is separate."""

from __future__ import annotations


def test_strategy_scores_per_avenue_not_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    st = MemoryStore()
    st.ensure_defaults()
    ss = st.load_json("strategy_scores.json")
    av = ss.setdefault("avenues", {})
    av["coinbase"] = {"mean_reversion": {"score": 0.9}}
    av["kalshi"] = {"mean_reversion": {"score": 0.1}}
    st.save_json("strategy_scores.json", ss)

    ss2 = st.load_json("strategy_scores.json")
    assert ss2["avenues"]["coinbase"]["mean_reversion"]["score"] == 0.9
    assert ss2["avenues"]["kalshi"]["mean_reversion"]["score"] == 0.1
