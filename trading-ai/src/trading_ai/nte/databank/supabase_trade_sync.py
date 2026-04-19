"""Supabase upserts for Trade Intelligence Databank — graceful without credentials."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from trading_ai.global_layer.supabase_env_keys import resolve_supabase_jwt_key
from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.nte.paths import nte_memory_dir

logger = logging.getLogger(__name__)

# Fixed id for upsert/delete diagnostic (idempotent overwrite; best-effort delete after probe).
_DIAG_PROBE_TRADE_ID = "__ezras_sync_diag_probe_v1__"

_RETRY_ATTEMPTS = 5  # initial try + 4 retries (deployment hard guarantee)


def local_unsynced_trades_path() -> Path:
    p = nte_memory_dir() / "local_unsynced_trades.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _format_response_error(exc: BaseException) -> str:
    """Stable, log-friendly error string (no secrets)."""
    parts: List[str] = [type(exc).__name__]
    msg = str(exc).strip()
    if msg:
        parts.append(msg[:500])
    resp = getattr(exc, "message", None) or getattr(exc, "details", None)
    if resp and str(resp).strip() and str(resp) != msg:
        parts.append(f"detail={str(resp)[:300]}")
    code = getattr(exc, "code", None)
    if code is not None:
        parts.append(f"code={code}")
    return " | ".join(parts)


def _sync_metrics_path() -> Path:
    return shark_state_path("supabase_sync_metrics.json")


def _read_sync_metrics() -> Dict[str, int]:
    p = _sync_metrics_path()
    if not p.is_file():
        return {"total": 0, "success": 0}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"total": 0, "success": 0}
        return {
            "total": int(raw.get("total") or 0),
            "success": int(raw.get("success") or 0),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {"total": 0, "success": 0}


def _write_sync_metrics(d: Dict[str, int]) -> None:
    p = _sync_metrics_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2), encoding="utf-8")


def record_sync_attempt_outcome(ok: bool) -> None:
    m = _read_sync_metrics()
    m["total"] = int(m.get("total") or 0) + 1
    if ok:
        m["success"] = int(m.get("success") or 0) + 1
    _write_sync_metrics(m)


def supabase_sync_rate() -> Optional[float]:
    m = _read_sync_metrics()
    t = int(m.get("total") or 0)
    if t <= 0:
        return None
    return float(m.get("success") or 0) / float(t)


def supabase_sync_rate_unhealthy() -> bool:
    """True when sample is large enough and success rate is below env threshold (default 95%)."""
    try:
        min_n = int((os.environ.get("SUPABASE_SYNC_MIN_SAMPLES") or "20").strip() or "20")
        thr = float((os.environ.get("SUPABASE_SYNC_RATE_MIN") or "0.95").strip() or "0.95")
    except ValueError:
        min_n, thr = 20, 0.95
    m = _read_sync_metrics()
    t = int(m.get("total") or 0)
    if t < min_n:
        return False
    r = supabase_sync_rate()
    return r is not None and r < thr


def queue_locally(row: Mapping[str, Any]) -> None:
    """Mandatory durability path when remote write is not confirmed."""
    _append_local_unsynced(row)


def _append_local_unsynced(row: Mapping[str, Any]) -> None:
    p = local_unsynced_trades_path()
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(row), default=str) + "\n")
    except Exception as exc:
        logger.warning("local_unsynced append failed: %s", _format_response_error(exc))


def _client_with_source() -> tuple[Any, Optional[str]]:
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key, key_source = resolve_supabase_jwt_key()
    if not url:
        logger.warning(
            "supabase_client_initialized: false reason=missing_SUPABASE_URL "
            "(set SUPABASE_URL for remote sync)"
        )
        return None, key_source
    if not key:
        logger.warning(
            "supabase_client_initialized: false reason=missing_jwt "
            "(set SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY)"
        )
        return None, key_source
    try:
        from supabase import create_client

        client = create_client(url, key)
        host = urlparse(url).netloc or "unknown_host"
        logger.info(
            "supabase_client_initialized: true key_source=%s url_host=%s",
            key_source,
            host,
        )
        return client, key_source
    except Exception as exc:
        logger.warning(
            "supabase_client_initialized: false create_client failed: %s",
            _format_response_error(exc),
        )
        return None, key_source


def _client():
    c, _ = _client_with_source()
    return c


def report_supabase_trade_sync_diagnostics() -> Dict[str, Any]:
    """
    Small operator report for live validation (no secrets).

    Reflects the same env resolution as :func:`_client` — ``SUPABASE_URL`` plus
    ``resolve_supabase_jwt_key()`` (``SUPABASE_KEY`` then ``SUPABASE_SERVICE_ROLE_KEY``).

    Returns keys: ``supabase_url_present``, ``key_source_used``, ``client_init_ok``,
    ``insert_probe_ok`` (minimal upsert to ``trade_events`` + delete when possible).
    On failure, ``client_init_error`` / ``insert_probe_error`` / ``insert_probe_cleanup_error``
    contain exception type names only.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key, key_src = resolve_supabase_jwt_key()
    out: Dict[str, Any] = {
        "supabase_url_present": bool(url),
        "key_source_used": key_src,
        "client_init_ok": False,
        "insert_probe_ok": False,
    }
    if not url or not key:
        return out
    try:
        from supabase import create_client

        client = create_client(url, key)
        out["client_init_ok"] = True
    except Exception as exc:
        out["client_init_error"] = type(exc).__name__
        return out
    try:
        from trading_ai.nte.databank.databank_schema import merge_defaults, row_for_supabase_trade_events

        raw = merge_defaults(
            {
                "trade_id": _DIAG_PROBE_TRADE_ID,
                "avenue_id": "A",
                "avenue_name": "coinbase",
                "asset": "DIAG-USD",
                "strategy_id": "supabase_sync_diagnostic",
                "route_chosen": "A",
                "regime": "validation",
                "timestamp_open": "1970-01-01T00:00:00+00:00",
                "timestamp_close": "1970-01-01T00:00:00+00:00",
            }
        )
        row = row_for_supabase_trade_events(raw, {})
        payload = _sanitize_row(dict(row))
        client.table("trade_events").upsert(payload, on_conflict="trade_id").execute()
        out["insert_probe_ok"] = True
        try:
            client.table("trade_events").delete().eq("trade_id", _DIAG_PROBE_TRADE_ID).execute()
        except Exception as exc_d:
            out["insert_probe_cleanup_error"] = type(exc_d).__name__
    except Exception as exc:
        out["insert_probe_error"] = type(exc).__name__
    return out


def describe_supabase_sync_client() -> Dict[str, Any]:
    """Debug-safe status for operators (no secrets). Does not log; probes client without caching."""
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key, key_source = resolve_supabase_jwt_key()
    host = urlparse(url).netloc if url else None
    out: Dict[str, Any] = {
        "supabase_url_configured": bool(url),
        "supabase_url_host": host,
        "supabase_jwt_source": key_source,
        "supabase_jwt_present": bool(key),
        "supabase_client_initialized": False,
    }
    if not url or not key:
        return out
    try:
        from supabase import create_client

        create_client(url, key)
        out["supabase_client_initialized"] = True
    except Exception as exc:
        out["client_probe_error"] = type(exc).__name__
    return out


def upsert_trade_event(
    row: Mapping[str, Any],
    *,
    queue_on_failure: bool = True,
) -> Dict[str, Any]:
    """
    Idempotent upsert on ``trade_id``.

    Returns a dict including ``success``, ``write_status`` (``success`` | ``failed``),
    ``key_source_used``, and retry metadata. On persistent failure, appends the row to
    ``local_unsynced_trades.jsonl`` when ``queue_on_failure`` is True.
    """
    key_source = resolve_supabase_jwt_key()[1]
    out: Dict[str, Any] = {
        "success": False,
        "write_status": "failed",
        "key_source_used": key_source,
        "attempts": 0,
        "queued_locally": False,
        "error": None,
    }
    client, ks = _client_with_source()
    out["key_source_used"] = ks or key_source
    logger.info("upsert_trade_event key_source_used=%s trade_id=%s", out["key_source_used"], row.get("trade_id"))

    if not client:
        err = "no_supabase_client"
        out["error"] = err
        logger.warning("upsert_trade_event failed: %s key_source_used=%s", err, out["key_source_used"])
        if queue_on_failure:
            queue_locally(row)
            out["queued_locally"] = True
        record_sync_attempt_outcome(False)
        return out

    payload = _sanitize_row(dict(row))
    tid = str(payload.get("trade_id") or "").strip()
    last_exc: Optional[BaseException] = None
    for attempt in range(_RETRY_ATTEMPTS):
        out["attempts"] = attempt + 1
        try:
            client.table("trade_events").upsert(payload, on_conflict="trade_id").execute()
            out["success"] = True
            out["write_status"] = "success"
            out["error"] = None
            logger.info(
                "upsert_trade_event write_status=success trade_id=%s attempt=%s key_source_used=%s",
                row.get("trade_id"),
                attempt + 1,
                out["key_source_used"],
            )
            if tid and tid != _DIAG_PROBE_TRADE_ID:
                if not verify_trade_exists(tid):
                    out["success"] = False
                    out["write_status"] = "verify_failed"
                    out["error"] = "post_write_verify_missing_row"
                    logger.error("upsert reported success but row missing trade_id=%s — queueing locally", tid)
                    queue_locally(row)
                    out["queued_locally"] = True
                    record_sync_attempt_outcome(False)
                    return out
            record_sync_attempt_outcome(True)
            return out
        except Exception as exc:
            last_exc = exc
            clean = _format_response_error(exc)
            logger.warning(
                "upsert_trade_event attempt %s/%s failed: %s key_source_used=%s",
                attempt + 1,
                _RETRY_ATTEMPTS,
                clean,
                out["key_source_used"],
            )
            if attempt + 1 < _RETRY_ATTEMPTS:
                delay = 0.5 * (2**attempt)
                time.sleep(delay)

    out["success"] = False
    out["write_status"] = "failed"
    out["error"] = _format_response_error(last_exc) if last_exc else "unknown"
    logger.warning(
        "upsert_trade_event write_status=failed trade_id=%s error=%s key_source_used=%s",
        row.get("trade_id"),
        out["error"],
        out["key_source_used"],
    )
    if queue_on_failure:
        queue_locally(row)
        out["queued_locally"] = True
    record_sync_attempt_outcome(False)
    return out


def flush_unsynced_trades() -> Dict[str, Any]:
    """
    Replay rows from ``local_unsynced_trades.jsonl``. Successful rows are removed; failures stay queued.
    Does not re-queue failures from this path (``queue_on_failure=False``).
    """
    p = local_unsynced_trades_path()
    result: Dict[str, Any] = {
        "attempted": 0,
        "flushed": 0,
        "remaining": 0,
        "parse_errors": 0,
    }
    if not p.is_file():
        return result
    raw_lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    result["attempted"] = len(raw_lines)
    remaining: List[str] = []
    for ln in raw_lines:
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            result["parse_errors"] = int(result.get("parse_errors") or 0) + 1
            remaining.append(ln)
            continue
        if not isinstance(row, dict):
            remaining.append(ln)
            continue
        r = upsert_trade_event(row, queue_on_failure=False)
        if r.get("success"):
            result["flushed"] = int(result["flushed"]) + 1
        else:
            remaining.append(json.dumps(row, default=str))

    result["remaining"] = len(remaining)
    try:
        if remaining:
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            tmp.replace(p)
        else:
            p.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("flush_unsynced_trades rewrite failed: %s", _format_response_error(exc))
    return result


def verify_trade_exists(trade_id: str) -> bool:
    """Alias for row existence check after write (PostgREST SELECT)."""
    return select_trade_event_exists(trade_id)


def select_trade_event_exists(trade_id: str) -> bool:
    """True if a row with ``trade_id`` exists (PostgREST SELECT)."""
    client = _client()
    if not client:
        return False
    tid = (trade_id or "").strip()
    if not tid:
        return False
    try:
        r = client.table("trade_events").select("trade_id").eq("trade_id", tid).limit(1).execute()
        data = getattr(r, "data", None)
        return isinstance(data, list) and len(data) > 0
    except Exception as exc:
        logger.warning("select_trade_event_exists failed: %s", _format_response_error(exc))
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
        logger.warning("upsert_rows %s failed: %s", table, _format_response_error(exc))
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
