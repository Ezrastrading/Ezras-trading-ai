"""Reward state snapshot."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.nte.memory.store import MemoryStore


def build_reward_report() -> Dict[str, Any]:
    store = MemoryStore()
    store.ensure_defaults()
    return store.load_json("reward_state.json")
