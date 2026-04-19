"""Goal progress from goals_state.json."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.nte.memory.store import MemoryStore


def build_goal_progress() -> Dict[str, Any]:
    store = MemoryStore()
    store.ensure_defaults()
    return store.load_json("goals_state.json")
