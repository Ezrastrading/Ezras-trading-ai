"""Speed progression — goal, ETA, accelerators, avenue mix."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.speed_progression_engine import SpeedProgressionEngine


def render_speed_progress_report(*, store: Optional[GlobalMemoryStore] = None, refresh: bool = True) -> Dict[str, Any]:
    st = store or GlobalMemoryStore()
    snap: Dict[str, Any]
    if refresh:
        snap = SpeedProgressionEngine(st).run_once()
    else:
        snap = st.load_json("speed_progression.json")
    return {
        "title": "Speed progression",
        "active_goal": snap.get("active_goal"),
        "current_status": snap.get("current_status"),
        "current_speed": snap.get("current_speed"),
        "blockers": snap.get("blockers"),
        "acceleration_options": snap.get("acceleration_options"),
        "best_path": snap.get("best_path"),
        "strongest_avenue": snap.get("strongest_avenue"),
        "weakest_avenue": snap.get("weakest_avenue"),
    }
