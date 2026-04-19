"""Coinbase writes local NTE memory before any global cross-avenue promotion."""

from __future__ import annotations

import json


def test_coinbase_trade_touches_local_trade_memory_first(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    st = MemoryStore()
    st.ensure_defaults()
    tm = st.load_json("trade_memory.json")
    tm.setdefault("trades", []).append(
        {
            "avenue": "coinbase",
            "product": "BTC-USD",
            "local_only_marker": True,
        }
    )
    st.save_json("trade_memory.json", tm)

    gpath = tmp_path / "global" / "trade_shadow.json"
    gpath.parent.mkdir(parents=True)
    gpath.write_text(json.dumps({"trades": []}))

    tm2 = st.load_json("trade_memory.json")
    assert any(t.get("local_only_marker") for t in tm2.get("trades", []))
    g = json.loads(gpath.read_text())
    assert g.get("trades") == []
