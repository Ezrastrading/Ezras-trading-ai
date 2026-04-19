"""Knowledge synthesis snapshot — internal + ranked external (research only)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.global_layer.data_knowledge_engine import DataKnowledgeEngine
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore


def render_knowledge_report(*, store: Optional[GlobalMemoryStore] = None, refresh: bool = True) -> Dict[str, Any]:
    st = store or GlobalMemoryStore()
    if refresh:
        DataKnowledgeEngine(st).run_once()
    kb = st.load_json("knowledge_base.json")
    si = st.load_json("strategy_intelligence.json")
    sr = st.load_json("source_rankings.json")
    rej = st.load_json("rejected_strategy_ideas.json")
    return {
        "title": "Knowledge intelligence",
        "global_truths_tail": (kb.get("global_truths") or [])[-5:],
        "strategy_families_tail": (si.get("strategy_families") or [])[-8:],
        "top_sources": (sr.get("sources") or [])[:8],
        "rejected_tail": (rej.get("rejected") or [])[-5:],
    }
