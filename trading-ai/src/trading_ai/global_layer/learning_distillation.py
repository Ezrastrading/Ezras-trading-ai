"""Local bot lessons vs shared approved learning — CEO/validator gate for shared."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.global_layer._bot_paths import global_layer_governance_dir
from trading_ai.global_layer.bot_memory import append_lesson, ensure_bot_memory_files


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def shared_learning_path() -> Path:
    return global_layer_governance_dir() / "shared_approved_learning.json"


def load_shared_learning() -> Dict[str, Any]:
    p = shared_learning_path()
    if not p.is_file():
        return {"truth_version": "shared_learning_v1", "lessons": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_shared_learning(data: Dict[str, Any]) -> None:
    p = shared_learning_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _iso()
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def propose_local_lesson(bot_id: str, lesson: Dict[str, Any]) -> Dict[str, Any]:
    ensure_bot_memory_files(bot_id)
    append_lesson(bot_id, {**lesson, "scope": "local"})
    return {"ok": True, "scope": "local"}


def propose_shared_lesson(bot_id: str, lesson: Dict[str, Any]) -> Dict[str, Any]:
    """Writes pending queue — requires approve_shared_lesson."""
    p = global_layer_governance_dir() / "shared_learning_pending.json"
    pend = {"items": []}
    if p.is_file():
        pend = json.loads(p.read_text(encoding="utf-8"))
    item = {**lesson, "proposed_by": bot_id, "proposed_at": _iso(), "status": "pending"}
    pend.setdefault("items", []).append(item)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pend, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "status": "pending_approval"}


def approve_shared_lesson(pending_index: int, approver: str) -> Tuple[bool, str]:
    p = global_layer_governance_dir() / "shared_learning_pending.json"
    if not p.is_file():
        return False, "no_pending"
    pend = json.loads(p.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = list(pend.get("items") or [])
    if pending_index < 0 or pending_index >= len(items):
        return False, "bad_index"
    lesson = items.pop(pending_index)
    lesson["approved_by"] = approver
    lesson["approved_at"] = _iso()
    shared = load_shared_learning()
    les = list(shared.get("lessons") or [])
    les.append(lesson)
    shared["lessons"] = les
    save_shared_learning(shared)
    pend["items"] = items
    p.write_text(json.dumps(pend, indent=2) + "\n", encoding="utf-8")
    return True, "ok"


def reject_shared_lesson(pending_index: int, approver: str, reason: str) -> Tuple[bool, str]:
    p = global_layer_governance_dir() / "shared_learning_pending.json"
    if not p.is_file():
        return False, "no_pending"
    pend = json.loads(p.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = list(pend.get("items") or [])
    if pending_index < 0 or pending_index >= len(items):
        return False, "bad_index"
    items.pop(pending_index)
    pend["items"] = items
    pend["last_reject"] = {"by": approver, "reason": reason, "at": _iso()}
    p.write_text(json.dumps(pend, indent=2) + "\n", encoding="utf-8")
    return True, "ok"
