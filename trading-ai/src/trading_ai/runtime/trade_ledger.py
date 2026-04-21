"""Append-only trade ledger (source of truth for PnL audit)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _ledger_rel() -> str:
    return "data/ledger/trade_ledger.jsonl"


def append_trade_ledger_line(
    record: Dict[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Append one JSON line. Ensures ``trade_id`` exists.
    Updates failsafe rolling PnL hints when ``pnl`` and timestamps are present.
    """
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.ensure_parent(_ledger_rel())
    line = dict(record)
    if not str(line.get("trade_id") or "").strip():
        line["trade_id"] = f"tl_{uuid.uuid4().hex[:16]}"
    line.setdefault("timestamp_open", None)
    line.setdefault("timestamp_close", None)
    line.setdefault("execution_status", "unknown")
    line.setdefault("validation_status", "unknown")
    line.setdefault("pnl", None)
    line.setdefault("failure_reason", None)
    p = ad.root() / _ledger_rel()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, default=str) + "\n")
    try:
        from trading_ai.safety.failsafe_guard import refresh_pnl_from_ledger

        refresh_pnl_from_ledger(runtime_root=runtime_root)
    except Exception:
        pass
    return line


def iter_ledger_lines(
    *, runtime_root: Optional[Path] = None, max_lines: int = 10_000
) -> Iterator[Dict[str, Any]]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    p = ad.root() / _ledger_rel()
    if not p.is_file():
        return
    with p.open("r", encoding="utf-8") as fh:
        for i, ln in enumerate(fh):
            if i >= max_lines:
                break
            ln = ln.strip()
            if not ln:
                continue
            try:
                j = json.loads(ln)
                if isinstance(j, dict):
                    yield j
            except json.JSONDecodeError:
                continue


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
