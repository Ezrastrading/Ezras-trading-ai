"""Disagreement log — evidence, authority chain, no conflicting live actions from orchestration."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.orchestration_paths import conflict_log_path


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_conflict(
    *,
    conflict_type: str,
    bot_a: str,
    bot_b: str,
    avenue: str,
    gate: str,
    evidence_a: Dict[str, Any],
    evidence_b: Dict[str, Any],
    resolution: str,
    escalated_to_ceo: bool = False,
) -> Dict[str, Any]:
    p = conflict_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "conflict_id": f"conf_{uuid.uuid4().hex[:12]}",
        "conflict_type": conflict_type,
        "bot_a": bot_a,
        "bot_b": bot_b,
        "avenue": avenue,
        "gate": gate,
        "evidence_a": evidence_a,
        "evidence_b": evidence_b,
        "resolution": resolution,
        "escalated_to_ceo": escalated_to_ceo,
        "created_at": _iso(),
        "honesty": "Does not execute trades; defers live routing to canonical execution authority path.",
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return row


def load_recent_conflicts(max_lines: int = 100) -> List[Dict[str, Any]]:
    p = conflict_log_path()
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-max_lines:]
    out: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
