"""Deterministic task routing keys and duplicate-task guard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.orchestration_paths import duplicate_task_guard_path
from trading_ai.global_layer.orchestration_registry_normalize import normalize_bot_record


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_routing_key(*, avenue: str, gate: str, route: str, bot_class: str, task_type: str) -> str:
    return f"{avenue}|{gate}|{route}|{bot_class}|{task_type}"


def owner_key_for_bot(bot: Dict[str, Any], task_type: str) -> str:
    b = normalize_bot_record(dict(bot))
    return task_routing_key(
        avenue=str(b.get("avenue") or ""),
        gate=str(b.get("gate") or "none"),
        route=str(b.get("route") or "default"),
        bot_class=str(b.get("bot_class") or "scanner_bot"),
        task_type=task_type,
    )


def load_task_guard(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or duplicate_task_guard_path()
    if not p.is_file():
        return {"truth_version": "duplicate_task_guard_v1", "active_tasks": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def save_task_guard(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or duplicate_task_guard_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _iso()
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def try_claim_task(
    *,
    bot_id: str,
    avenue: str,
    gate: str,
    route: str,
    bot_class: str,
    task_type: str,
    task_id: str,
) -> Tuple[bool, str]:
    """One active task per routing key at a time (deterministic)."""
    key = task_routing_key(avenue=avenue, gate=gate, route=route, bot_class=bot_class, task_type=task_type)
    g = load_task_guard()
    active: Dict[str, Any] = dict(g.get("active_tasks") or {})
    cur = active.get(key)
    if cur and str(cur.get("bot_id")) != bot_id and str(cur.get("task_id")) != task_id:
        return False, f"task_slot_held:{key}"
    active[key] = {"bot_id": bot_id, "task_id": task_id, "claimed_at": _iso()}
    g["active_tasks"] = active
    save_task_guard(g)
    return True, "ok"


def release_task(
    *,
    avenue: str,
    gate: str,
    route: str,
    bot_class: str,
    task_type: str,
    task_id: str,
) -> None:
    key = task_routing_key(avenue=avenue, gate=gate, route=route, bot_class=bot_class, task_type=task_type)
    g = load_task_guard()
    active = dict(g.get("active_tasks") or {})
    cur = active.get(key)
    if cur and str(cur.get("task_id")) == task_id:
        del active[key]
    g["active_tasks"] = active
    save_task_guard(g)
