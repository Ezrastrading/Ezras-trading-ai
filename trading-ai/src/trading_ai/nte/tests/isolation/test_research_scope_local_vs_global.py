"""Research memory sandbox vs promoted lists stay isolated per store."""

from __future__ import annotations


def test_sandbox_list_not_auto_promoted(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    st = MemoryStore()
    st.ensure_defaults()
    rm = st.load_json("research_memory.json")
    rm.setdefault("sandbox_strategies", []).append({"id": "sb1"})
    rm.setdefault("promoted", [])
    st.save_json("research_memory.json", rm)

    rm2 = st.load_json("research_memory.json")
    assert "sb1" in str(rm2.get("sandbox_strategies"))
    assert rm2.get("promoted") == []
