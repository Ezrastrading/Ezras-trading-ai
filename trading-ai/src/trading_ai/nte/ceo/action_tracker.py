"""Persist CEO session actions and unresolved issues for follow-up reviews."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.paths import nte_ceo_action_log_path, nte_unresolved_issues_path
from trading_ai.nte.utils.atomic_json import atomic_write_json


def _load(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "actions": []}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"schema_version": 1, "actions": []}
    except Exception:
        return {"schema_version": 1, "actions": []}


def append_action(
    *,
    session_id: str,
    avenue_scope: str,
    action_type: str,
    description: str,
    reason: str,
    priority: str,
    owner_module: str,
    review_due_at: Optional[float] = None,
    metric_to_watch: str = "",
    expected_effect: str = "",
    path: Optional[Path] = None,
) -> str:
    p = path or nte_ceo_action_log_path()
    data = _load(p)
    actions: List[Dict[str, Any]] = list(data.get("actions") or [])
    aid = str(uuid.uuid4())
    actions.append(
        {
            "action_id": aid,
            "session_id": session_id,
            "timestamp": time.time(),
            "avenue_scope": avenue_scope,
            "action_type": action_type,
            "description": description,
            "reason": reason,
            "priority": priority,
            "owner_module": owner_module,
            "status": "open",
            "review_due_at": review_due_at,
            "metric_to_watch": metric_to_watch,
            "expected_effect": expected_effect,
            "actual_effect": "",
        }
    )
    data["actions"] = actions[-5000:]
    atomic_write_json(p, data)
    return aid


def list_open_actions(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    data = _load(path or nte_ceo_action_log_path())
    out = [a for a in (data.get("actions") or []) if isinstance(a, dict)]
    return [a for a in out if str(a.get("status")) in ("open", "in_progress")]


def update_action_status(
    action_id: str,
    status: str,
    *,
    actual_effect: str = "",
    path: Optional[Path] = None,
) -> None:
    p = path or nte_ceo_action_log_path()
    data = _load(p)
    actions: List[Dict[str, Any]] = list(data.get("actions") or [])
    for a in actions:
        if str(a.get("action_id")) == action_id:
            a["status"] = status
            if actual_effect:
                a["actual_effect"] = actual_effect
            break
    data["actions"] = actions
    atomic_write_json(p, data)


def append_unresolved_issue(
    summary: str,
    *,
    avenue_scope: str = "global",
    severity: str = "medium",
    path: Optional[Path] = None,
) -> str:
    p = path or nte_unresolved_issues_path()
    data = _load(p)
    if "issues" not in data:
        data["issues"] = []
    issues: List[Dict[str, Any]] = list(data.get("issues") or [])
    iid = str(uuid.uuid4())
    issues.append(
        {
            "issue_id": iid,
            "ts": time.time(),
            "avenue_scope": avenue_scope,
            "severity": severity,
            "summary": summary,
            "status": "open",
        }
    )
    data["issues"] = issues[-2000:]
    atomic_write_json(p, data)
    return iid
