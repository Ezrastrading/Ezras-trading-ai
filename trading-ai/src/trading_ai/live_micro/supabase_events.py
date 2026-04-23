from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.global_layer.supabase_env_keys import resolve_supabase_jwt_key

logger = logging.getLogger(__name__)


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def supabase_error_log_path(runtime_root: Path) -> Path:
    root = Path(runtime_root).resolve()
    return root / "data" / "control" / "live_micro_supabase_write_errors.jsonl"


def _client() -> Tuple[Optional[Any], Dict[str, Any]]:
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key, key_src = resolve_supabase_jwt_key()
    meta = {"supabase_url_present": bool(url), "key_source": key_src, "jwt_present": bool(key)}
    if not url or not key:
        return None, {**meta, "client_ok": False, "reason": "missing_supabase_credentials"}
    try:
        from supabase import create_client

        return create_client(url, key), {**meta, "client_ok": True}
    except Exception as exc:
        return None, {**meta, "client_ok": False, "reason": f"create_client_failed:{type(exc).__name__}"}


def maybe_write_live_micro_event(
    *,
    runtime_root: Path,
    event: str,
    product_id: Optional[str] = None,
    order_id: Optional[str] = None,
    position_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
) -> bool:
    """
    Non-blocking Supabase write. Uses `public.live_micro_events` if present.
    If it fails, writes a local error JSONL and returns False.
    """
    root = Path(runtime_root).resolve()
    client, meta = _client()
    row = {
        "event_id": (dedupe_key or ""),
        "ts_unix": float(time.time()),
        "event": str(event),
        "product_id": (str(product_id).strip().upper() if product_id else None),
        "order_id": (str(order_id).strip() if order_id else None),
        "position_id": (str(position_id).strip() if position_id else None),
        "payload": payload or {},
    }
    if client is None or not meta.get("client_ok"):
        _append_jsonl(
            supabase_error_log_path(root),
            {"ts": time.time(), "event": "supabase_write_skipped", "reason": meta.get("reason"), "meta": meta, "row": row},
        )
        return False
    try:
        # Idempotency: if event_id provided, upsert on conflict.
        if row["event_id"]:
            client.table("live_micro_events").upsert(row, on_conflict="event_id").execute()
        else:
            client.table("live_micro_events").insert(row).execute()
        return True
    except Exception as exc:
        _append_jsonl(
            supabase_error_log_path(root),
            {
                "ts": time.time(),
                "event": "supabase_write_failed",
                "error": type(exc).__name__,
                "meta": meta,
                "row": row,
            },
        )
        logger.debug("live_micro supabase write failed", exc_info=True)
        return False

