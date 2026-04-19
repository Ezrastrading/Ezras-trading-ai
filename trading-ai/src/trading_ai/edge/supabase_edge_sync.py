"""Optional mirror of edge registry rows to Supabase (graceful without credentials)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping

from trading_ai.nte.databank.supabase_trade_sync import _client, _sanitize_row

logger = logging.getLogger(__name__)


def mirror_edge_registry_row(edge_row: Mapping[str, Any]) -> bool:
    """Upsert one edge into ``edge_registry`` table if configured (see ``supabase/edge_validation_engine.sql``)."""
    client = _client()
    if not client:
        return False
    try:
        payload = _sanitize_row(dict(edge_row))
        payload.setdefault("schema_version", "1.0.0")
        client.table("edge_registry").upsert(payload, on_conflict="edge_id").execute()
        return True
    except Exception as exc:
        # Table may not exist until migration is applied — local registry remains source of truth.
        logger.debug("mirror_edge_registry_row: %s", exc)
        return False
