"""Structured inter-bot messages only — no freeform agent chatter."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.global_layer._bot_paths import global_layer_governance_dir


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def messages_store_path() -> Path:
    return global_layer_governance_dir() / "bot_messages.jsonl"


def enqueue_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    p = messages_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    mid = str(msg.get("message_id") or "").strip() or f"msg_{uuid.uuid4().hex[:16]}"
    row = dict(msg)
    row.setdefault("message_id", mid)
    row.setdefault("created_at", _iso())
    row.setdefault("task_id", "none")
    row.setdefault("confidence", None)
    row.setdefault("evidence_refs", [])
    row.setdefault("expires_at", None)
    required = ("source_bot", "destination_bot", "avenue", "gate", "message_type", "payload")
    for k in required:
        if k not in row or row[k] is None:
            raise ValueError(f"message_missing_field:{k}")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return row


def validate_message_schema(row: Dict[str, Any]) -> bool:
    for k in ("message_id", "source_bot", "destination_bot", "avenue", "gate", "task_id", "message_type", "payload", "created_at"):
        if k not in row:
            return False
    return True
