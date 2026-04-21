"""Canonical task objects — file-backed JSONL for auditability."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer._bot_paths import global_layer_governance_dir
from trading_ai.global_layer.bot_types import TaskStatus


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tasks_store_path() -> Path:
    return global_layer_governance_dir() / "tasks.jsonl"


def append_task(
    task: Dict[str, Any],
    *,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    p = path or tasks_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tid = str(task.get("task_id") or "").strip() or f"task_{uuid.uuid4().hex[:16]}"
    row = dict(task)
    row.setdefault("task_id", tid)
    row.setdefault("status", TaskStatus.PENDING.value)
    row.setdefault("created_at", _iso())
    row.setdefault("priority", 0)
    line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line)
    rt = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if rt:
        mirror = Path(rt).expanduser().resolve() / "data" / "control" / "tasks.jsonl"
        try:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            with mirror.open("a", encoding="utf-8") as mh:
                mh.write(line)
        except OSError:
            pass
    return row


def load_all_tasks(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = path or tasks_store_path()
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def canonical_task_template(
    *,
    avenue: str,
    gate: str,
    task_type: str,
    source_bot_id: str,
    assigned_bot_id: str,
    evidence_ref: str,
    expires_in_hours: int = 24,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "task_id": f"task_{uuid.uuid4().hex[:16]}",
        "avenue": avenue,
        "gate": gate,
        "task_type": task_type,
        "source_bot_id": source_bot_id,
        "assigned_bot_id": assigned_bot_id,
        "backup_bot_id": None,
        "status": TaskStatus.PENDING.value,
        "priority": 0,
        "evidence_ref": evidence_ref,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=expires_in_hours)).isoformat(),
    }
