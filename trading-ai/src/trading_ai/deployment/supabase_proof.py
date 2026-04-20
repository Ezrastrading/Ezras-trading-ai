"""
Supabase write proof for a trade id: upsert path, remote row, optional flush recovery.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from trading_ai.deployment.paths import supabase_proof_jsonl_path
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.nte.databank.supabase_trade_sync import (
    flush_unsynced_trades,
    select_trade_event_exists,
    select_trade_event_exists_detail,
    upsert_trade_event,
)

_VERIFY_ATTEMPTS = 8
"""Validation trade proof: allow time for PostgREST commit + optional queue flush."""


def prove_supabase_write(
    trade_id: str,
    *,
    append_log: bool = True,
    allow_flush_recovery: bool = True,
) -> Dict[str, Any]:
    """
    Verify remote row exists for ``trade_id`` (remote truth, not local queue hope).

    If missing, flush unsynced queue and poll briefly — ``recovered`` when row appears after flush.
    Fails if row never appears within the proof window after retries.
    Appends one JSON line to ``data/deployment/supabase_proof.jsonl``.

    Diagnostic probe success (``__ezras_sync_diag_probe_v1__``) is unrelated — this checks the
    **actual** validation ``trade_id`` only.
    """
    tid = (trade_id or "").strip()
    rec: Dict[str, Any] = {
        "ts": iso_now(),
        "trade_id": tid,
        "last_verified_trade_id": tid,
        "row_exists_initial": False,
        "recovered": False,
        "flush_result": None,
        "verify_attempts": 0,
        "supabase_proof_ok": False,
        "verify_query": "trade_events.select(trade_id).eq(trade_id, <id>).limit(1)",
        "last_select_error": None,
        "probe_note": "insert_probe_ok uses a separate diagnostic trade_id — proof here is only for the given trade_id",
    }
    if not tid:
        rec["error"] = "empty_trade_id"
        _append_line(rec, append_log)
        return rec

    d0 = select_trade_event_exists_detail(tid)
    exists = bool(d0.get("exists"))
    rec["row_exists_initial"] = exists
    rec["last_select_error"] = d0.get("error")
    initial_exists = exists

    for attempt in range(_VERIFY_ATTEMPTS):
        rec["verify_attempts"] = attempt + 1
        d = select_trade_event_exists_detail(tid)
        exists = bool(d.get("exists"))
        rec["last_select_error"] = d.get("error")
        if exists:
            break
        if allow_flush_recovery:
            rec["flush_result"] = flush_unsynced_trades()
        delay = min(3.0, 0.35 * (2**attempt))
        rec["last_wait_sec"] = delay
        time.sleep(delay)
        d2 = select_trade_event_exists_detail(tid)
        exists = bool(d2.get("exists"))
        rec["last_select_error"] = d2.get("error")
        if exists:
            break

    rec["recovered"] = bool(exists and not initial_exists)
    rec["supabase_proof_ok"] = bool(exists)
    _append_line(rec, append_log)
    return rec


def _append_line(rec: Dict[str, Any], append_log: bool) -> None:
    if not append_log:
        return
    p = supabase_proof_jsonl_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def prove_supabase_write_with_row(
    row: Dict[str, Any],
    *,
    append_log: bool = True,
) -> Dict[str, Any]:
    """Upsert ``row`` then prove existence (for tests / explicit replay)."""
    tid = str(row.get("trade_id") or "").strip()
    up = upsert_trade_event(row, queue_on_failure=True)
    rec: Dict[str, Any] = {
        "ts": iso_now(),
        "trade_id": tid,
        "upsert": up,
        "row_exists_initial": bool(up.get("success")),
    }
    exists = select_trade_event_exists(tid)
    rec["row_exists_after_upsert"] = exists
    if not exists:
        flush_res = flush_unsynced_trades()
        rec["flush_result"] = flush_res
        exists = select_trade_event_exists(tid)
        rec["recovered"] = exists and not rec["row_exists_after_upsert"]
    rec["supabase_proof_ok"] = exists
    _append_line(rec, append_log)
    return rec
