"""Aggregate health signals into ``system_health.json`` for CEO / ops."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.nte.hardening.mode_context import ExecutionMode, get_execution_mode, live_explicitly_enabled
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.paths import nte_system_health_path
from trading_ai.nte.utils.atomic_json import atomic_write_json


def build_system_health(
    *,
    ws_connected: Optional[bool] = None,
    last_market_ts: Optional[float] = None,
    last_order_ts: Optional[float] = None,
    last_memory_write_ts: Optional[float] = None,
    last_ceo_ts: Optional[float] = None,
    last_goal_ts: Optional[float] = None,
    avenue_pause: Optional[Dict[str, bool]] = None,
    global_pause: bool = False,
) -> Dict[str, Any]:
    mode = get_execution_mode()
    healthy = True
    degraded: list = []
    if global_pause:
        healthy = False
        degraded.append("global_pause")
    if ws_connected is False:
        healthy = False
        degraded.append("websocket")
    if last_market_ts and (time.time() - last_market_ts) > 120:
        healthy = False
        degraded.append("stale_market_data")

    exec_continue = not global_pause and healthy
    exec_pause = not exec_continue

    return {
        "schema_version": 1,
        "ts": time.time(),
        "healthy": healthy,
        "degraded_components": degraded,
        "execution_should_continue": exec_continue,
        "execution_should_pause": exec_pause,
        "human_review_recommended": len(degraded) > 0,
        "mode": mode.value,
        "live_explicit": live_explicitly_enabled(),
        "live_eligible": mode == ExecutionMode.LIVE and live_explicitly_enabled(),
        "global_pause": global_pause,
        "avenue_pause": avenue_pause or {},
        "timestamps": {
            "last_market_data": last_market_ts,
            "last_order_event": last_order_ts,
            "last_memory_write": last_memory_write_ts,
            "last_ceo_session": last_ceo_ts,
            "last_goal_update": last_goal_ts,
        },
        "websocket_connected": ws_connected,
    }


def write_system_health(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    atomic_write_json(path or nte_system_health_path(), data)


def refresh_default_health() -> Dict[str, Any]:
    store = MemoryStore()
    store.ensure_defaults()
    gm = store.load_json("goals_state.json")
    rv = store.load_json("review_state.json")
    last_goal = None
    try:
        last_goal = gm.get("updated")
    except Exception:
        pass
    h = build_system_health(
        last_goal_ts=None,
        last_ceo_ts=rv.get("last_daily") if isinstance(rv, dict) else None,
    )
    write_system_health(h)
    return h
