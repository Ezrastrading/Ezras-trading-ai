"""Persistent counters for live execution quality (fills vs stale cancels)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from trading_ai.nte.paths import nte_memory_dir

logger = logging.getLogger(__name__)


def counters_path() -> Path:
    p = nte_memory_dir() / "execution_counters.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def default_counters() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "limit_entries_placed": 0,
        "limit_entries_filled": 0,
        "stale_pending_canceled": 0,
        "market_entries": 0,
    }


def load_counters() -> Dict[str, Any]:
    p = counters_path()
    if not p.is_file():
        return default_counters()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return default_counters()
        d = default_counters()
        d.update({k: raw[k] for k in d if k in raw})
        return d
    except Exception as exc:
        logger.debug("execution_counters load: %s", exc)
        return default_counters()


def save_counters(data: Dict[str, Any]) -> None:
    p = counters_path()
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def bump(field: str, delta: int = 1) -> Dict[str, Any]:
    d = load_counters()
    cur = int(d.get(field) or 0)
    d[field] = cur + delta
    save_counters(d)
    return d
