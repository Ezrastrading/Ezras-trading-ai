"""
Implementation governor — classifies candidate changes and queues non-live-safe work.

No live mutation without promotion contracts and truth writers.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal

from trading_ai.global_layer.automation_queues import ensure_automation_queues_initialized
from trading_ai.global_layer.orchestration_paths import implementation_governor_state_path, implementation_queue_path

ChangeClass = Literal[
    "research_finding",
    "candidate_change",
    "shadow_only",
    "replay_only",
    "supervised_eligible",
    "autonomous_eligible",
]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_implementation_governor_state() -> Dict[str, Any]:
    p = implementation_governor_state_path()
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    payload = {
        "truth_version": "implementation_governor_state_v1",
        "updated_at": _iso(),
        "last_promotion_candidates": [],
        "honesty": "autonomous_eligible requires objective contract + gates — never implied by this file alone.",
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def queue_implementation_item(
    *,
    title: str,
    change_class: ChangeClass,
    evidence_refs: List[str],
    risk_notes: str = "",
) -> Dict[str, Any]:
    """Append one item to implementation queue (deterministic id)."""
    ensure_automation_queues_initialized()
    qpath = implementation_queue_path()
    cur = json.loads(qpath.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = list(cur.get("items") or [])
    row = {
        "item_id": f"impl_{uuid.uuid4().hex[:12]}",
        "title": title,
        "change_class": change_class,
        "evidence_refs": evidence_refs,
        "risk_notes": risk_notes,
        "created_at": _iso(),
    }
    items.append(row)
    cur["items"] = items
    cur["updated_at"] = _iso()
    qpath.write_text(json.dumps(cur, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return row
