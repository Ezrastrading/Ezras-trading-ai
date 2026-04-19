"""Persistence helpers for global speed / progress JSON (uses :class:`GlobalMemoryStore`)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore


def load_speed_snapshot(store: Optional[GlobalMemoryStore] = None) -> Dict[str, Any]:
    st = store or GlobalMemoryStore()
    return st.load_json("speed_progression.json")


def save_speed_snapshot(data: Dict[str, Any], store: Optional[GlobalMemoryStore] = None) -> None:
    st = store or GlobalMemoryStore()
    st.save_json("speed_progression.json", data)
