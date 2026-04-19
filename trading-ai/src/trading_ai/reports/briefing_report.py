"""Tactical briefing — short internal + external sides."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.global_layer.briefing_engine import BriefingEngine
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore


def render_briefing_report(*, store: Optional[GlobalMemoryStore] = None, refresh: bool = True) -> Dict[str, Any]:
    st = store or GlobalMemoryStore()
    if refresh:
        out = BriefingEngine(st).run_once()
    else:
        out = {"text": "", "active_goal": None, "top_3_actions": [], "top_3_risks": [], "top_3_research_priorities": []}
    return {"title": "Briefing", **out}
