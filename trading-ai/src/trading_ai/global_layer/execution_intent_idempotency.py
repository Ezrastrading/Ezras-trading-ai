"""
Replay-safe execution intent IDs — append-only ledger under orchestration root.

Venue code should compute ``intent_id`` deterministically (hash of stable fields) and call
:func:`claim_execution_intent` before placing an order. Duplicate claims return ``allowed: false``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.global_layer.orchestration_paths import execution_intent_ledger_path


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deterministic_intent_id(
    *,
    bot_id: str,
    signal_time_iso: str,
    symbol: str,
    intent: str,
    avenue: str,
    gate: str,
    route: str = "default",
) -> str:
    """Stable id for idempotency / dedupe (not cryptographic secrecy)."""
    raw = "|".join(
        [
            str(bot_id).strip(),
            str(signal_time_iso).strip(),
            str(symbol).strip().upper(),
            str(intent).strip().lower(),
            str(avenue).strip(),
            str(gate).strip(),
            str(route).strip(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def claim_execution_intent(
    intent_id: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    path: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    If ``intent_id`` already recorded, return (False, duplicate_intent, row).
    Otherwise append and return (True, ok, row).
    """
    p = path or execution_intent_ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_tail_intent_ids(p, max_scan_lines=50_000)
    if intent_id in existing:
        return False, "duplicate_intent", {"intent_id": intent_id, "honesty": "reject_retry_or_replay"}

    row = {
        "truth_version": "execution_intent_ledger_row_v1",
        "intent_id": intent_id,
        "claimed_at": _iso(),
        "meta": dict(meta or {}),
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return True, "ok", row


def _load_tail_intent_ids(path: Path, *, max_scan_lines: int) -> set[str]:
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    tail = lines[-max_scan_lines:] if len(lines) > max_scan_lines else lines
    out: set[str] = set()
    for ln in tail:
        try:
            d = json.loads(ln)
            iid = str(d.get("intent_id") or "").strip()
            if iid:
                out.add(iid)
        except json.JSONDecodeError:
            continue
    return out


def intent_ledger_stats(*, path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or execution_intent_ledger_path()
    if not p.is_file():
        return {"truth_version": "execution_intent_stats_v1", "line_count": 0}
    n = sum(1 for _ in p.open(encoding="utf-8"))
    return {"truth_version": "execution_intent_stats_v1", "line_count": n, "path": str(p)}
