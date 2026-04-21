"""Append-only audit — who proposed / approved, evidence, confidence, venue/gate snapshot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer._bot_paths import global_layer_governance_dir


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit_log_path() -> Path:
    return global_layer_governance_dir() / "audit_trail.jsonl"


def append_audit_event(
    event_type: str,
    payload: Dict[str, Any],
    *,
    bot_id: str,
    approved_by: Optional[str],
    evidence_refs: Optional[List[str]] = None,
    confidence: Optional[float] = None,
    venue_state: Optional[Dict[str, Any]] = None,
    gate_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    p = audit_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "truth_version": "audit_event_v1",
        "event_type": event_type,
        "bot_id": bot_id,
        "approved_by": approved_by,
        "evidence_refs": list(evidence_refs or []),
        "confidence": confidence,
        "venue_state": venue_state or {},
        "gate_state": gate_state or {},
        "payload": payload,
        "created_at": _iso(),
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return row


def load_recent_events(max_lines: int = 200) -> List[Dict[str, Any]]:
    p = audit_log_path()
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
