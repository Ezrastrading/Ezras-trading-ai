"""Supabase upserts for Trade Intelligence Databank — graceful without credentials."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)


def _client():
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_KEY") or "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client

        return create_client(url, key)
    except Exception as exc:
        logger.warning("supabase create_client failed: %s", exc)
        return None


def upsert_trade_event(row: Mapping[str, Any]) -> bool:
    """Idempotent upsert on trade_id."""
    client = _client()
    if not client:
        return False
    try:
        payload = _sanitize_row(dict(row))
        client.table("trade_events").upsert(payload, on_conflict="trade_id").execute()
        return True
    except Exception as exc:
        logger.warning("upsert_trade_event failed: %s", exc)
        return False


def upsert_rows(table: str, rows: List[Mapping[str, Any]], on_conflict: str) -> bool:
    client = _client()
    if not client or not rows:
        return False
    try:
        clean = [_sanitize_row(dict(r)) for r in rows]
        client.table(table).upsert(clean, on_conflict=on_conflict).execute()
        return True
    except Exception as exc:
        logger.warning("upsert_rows %s failed: %s", table, exc)
        return False


def _sanitize_row(d: Dict[str, Any]) -> Dict[str, Any]:
    """Remove keys that are None if needed — PostgREST accepts null."""
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, float) and (v != v):  # NaN
            out[k] = None
        else:
            out[k] = v
    return out


def sync_summary_batch(table: str, rows: List[Mapping[str, Any]], conflict_key: str) -> bool:
    """Upsert summary rows (daily, strategy, avenue, etc.)."""
    return upsert_rows(table, rows, on_conflict=conflict_key)
