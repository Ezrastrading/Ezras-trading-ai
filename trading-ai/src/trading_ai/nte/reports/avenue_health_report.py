"""Per-avenue health summary (uses memory + goals)."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.nte.memory.store import MemoryStore


def build_avenue_health(avenue: str = "coinbase") -> Dict[str, Any]:
    store = MemoryStore()
    store.ensure_defaults()
    scores = store.load_json("strategy_scores.json")
    goals = store.load_json("goals_state.json")
    return {
        "avenue": avenue,
        "strategy_snapshot": (scores.get("avenues") or {}).get(avenue, {}),
        "goals_ref": goals.get("updated"),
    }
