"""
Persistent learning memory — strengths, mistakes, venue notes, CEO history pointers.

Read/write JSON only; does not change execution parameters by itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.learning.paths import trading_memory_path

DEFAULT_MEMORY: Dict[str, Any] = {
    "schema_version": "1.0",
    "updated_at": None,
    "repeated_mistakes": [],
    "repeated_strengths": [],
    "edge_improvements": [],
    "venue_lessons": {"coinbase": [], "kalshi": [], "options": []},
    "execution_lessons": [],
    "discipline_lessons": [],
    "ceo_recommendations_history": [],
    "recommendations_that_worked": [],
    "recommendations_that_failed": [],
    "avenue_summaries": {
        "coinbase": {"what_works": [], "what_fails": [], "conditions": [], "execution_problems": []},
        "kalshi": {"what_works": [], "what_fails": [], "conditions": [], "execution_problems": []},
        "options": {"what_works": [], "what_fails": [], "conditions": [], "execution_problems": []},
    },
}


def load_trading_memory(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or trading_memory_path()
    if not p.is_file():
        return dict(DEFAULT_MEMORY)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(DEFAULT_MEMORY)
        out = dict(DEFAULT_MEMORY)
        out.update(raw)
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return dict(DEFAULT_MEMORY)


def save_trading_memory(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or trading_memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    d = dict(data)
    d["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "schema_version" not in d:
        d["schema_version"] = "1.0"
    p.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")


def append_unique(lst: List[str], item: str, *, cap: int = 50) -> None:
    s = str(item).strip()
    if not s:
        return
    if s in lst:
        lst.remove(s)
    lst.insert(0, s)
    del lst[cap:]
