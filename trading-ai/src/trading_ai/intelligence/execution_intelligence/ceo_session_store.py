"""Structured CEO session persistence — JSON lines under NTE memory (markdown remains human mirror)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.paths import nte_memory_dir

HISTORY_NAME = "ceo_session_history.jsonl"
STRUCTURED_INDEX = "ceo_session_structured_latest.json"


def _path(name: str) -> Path:
    return nte_memory_dir() / name


def append_structured_ceo_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Append one JSON object per line to ceo_session_history.jsonl.
    Also writes ceo_session_structured_latest.json for fast readers.
    """
    rec = dict(payload)
    rec.setdefault("session_id", str(uuid.uuid4()))
    rec.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    rec.setdefault("truth_version", "ceo_structured_session_v1")

    p = _path(HISTORY_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, default=str) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)

    latest = _path(STRUCTURED_INDEX)
    latest.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    return rec


def load_latest_structured_session() -> Optional[Dict[str, Any]]:
    p = _path(STRUCTURED_INDEX)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def tail_structured_sessions(n: int = 20) -> List[Dict[str, Any]]:
    p = _path(HISTORY_NAME)
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                out.append(d)
        except json.JSONDecodeError:
            continue
    return out


def build_session_record_from_eie(
    *,
    active_goal: str,
    progress: Dict[str, Any],
    daily_plan: Dict[str, Any],
    avenue_focus: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Shape for orchestration / EIE consumers."""
    return {
        "active_goal": active_goal,
        "top_blockers": list(progress.get("blockers") or [])[:12],
        "today_plan": list(daily_plan.get("today_focus") or [])[:20],
        "tomorrow_plan": list(daily_plan.get("tomorrow_focus") or [])[:20],
        "avenue_focus": list(avenue_focus or []),
        "risk_posture": daily_plan.get("mode"),
        "capital_posture": "advisory_only",
        "scaling_posture": daily_plan.get("mode"),
        "honesty": "Advisory session record — does not change risk or permissions.",
    }
