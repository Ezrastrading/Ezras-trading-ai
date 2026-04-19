"""Record per-trade write verification (local + Supabase + summaries)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.nte.databank.local_trade_store import (
    load_aggregate,
    path_write_verification,
    save_aggregate,
)

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_verification_state() -> Dict[str, Any]:
    default = {"entries": [], "last": None, "updated": None}
    return load_aggregate(path_write_verification(), default)


def record_trade_write_verification(
    trade_id: str,
    stages: Mapping[str, bool],
    *,
    partial_failure: bool,
    error_messages: Optional[List[str]] = None,
    retry_status: str = "none",
) -> Dict[str, Any]:
    """Append verification record and update last pointer."""
    state = load_verification_state()
    entries = state.get("entries")
    if not isinstance(entries, list):
        entries = []
    entry = {
        "trade_id": trade_id,
        "timestamp": _iso(),
        "stages": dict(stages),
        "partial_failure": partial_failure,
        "errors": list(error_messages or []),
        "retry_status": retry_status,
    }
    entries.append(entry)
    state["entries"] = entries[-2000:]
    state["last"] = entry
    state["updated"] = _iso()
    save_aggregate(path_write_verification(), state)
    if partial_failure:
        logger.warning("trade write partial failure %s: %s", trade_id, entry)
    return entry


def summarize_last_n_failures(n: int = 20) -> List[Dict[str, Any]]:
    state = load_verification_state()
    entries = state.get("entries") or []
    if not isinstance(entries, list):
        return []
    fails = [e for e in entries if isinstance(e, dict) and e.get("partial_failure")]
    return fails[-n:]
