"""Research sandbox / promotion snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.paths import nte_promotion_log_path


def build_research_report() -> Dict[str, Any]:
    store = MemoryStore()
    store.ensure_defaults()
    rm = store.load_json("research_memory.json")
    pl = nte_promotion_log_path()
    prom: Dict[str, Any] = {}
    if pl.is_file():
        try:
            prom = json.loads(pl.read_text(encoding="utf-8"))
        except Exception:
            prom = {}
    return {"research_memory": rm, "promotion_log_tail": (prom.get("events") or [])[-20:]}
