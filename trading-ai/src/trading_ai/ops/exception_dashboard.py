"""
Backend data layer for operator / UI exception visibility.

State: ``{EZRAS_RUNTIME_ROOT}/state/exception_dashboard_state.json``
Log: ``{EZRAS_RUNTIME_ROOT}/logs/exception_dashboard_log.md``
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 1

Category = Literal[
    "blocked_trade",
    "reduced_trade",
    "reconciliation_drift",
    "truth_sync_drift",
    "lockout_active",
    "missing_data",
    "malformed_input",
    "fee_anomaly",
    "execution_anomaly",
    "stale_state",
]
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _state_path() -> Path:
    return runtime_root() / "state" / "exception_dashboard_state.json"


def _log_path() -> Path:
    return runtime_root() / "logs" / "exception_dashboard_log.md"


def _default_state() -> Dict[str, Any]:
    return {"version": _STATE_VERSION, "entries": []}


def _load() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_state()
        out = _default_state()
        out.update(raw)
        out.setdefault("entries", [])
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_state()


def _save(data: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = _STATE_VERSION
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _append_log(row: Dict[str, Any]) -> None:
    try:
        _log_path().parent.mkdir(parents=True, exist_ok=True)
        ts = row.get("timestamp") or datetime.now(timezone.utc).isoformat()
        block = f"\n## {ts}\n\n```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("exception_dashboard log append failed: %s", exc)


def add_exception_event(
    *,
    category: str,
    message: str,
    severity: Severity = "MEDIUM",
    related_trade_id: Optional[str] = None,
    requires_review: bool = True,
    resolved: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry = {
        "id": secrets.token_hex(8),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": str(category),
        "severity": str(severity).upper(),
        "message": str(message),
        "related_trade_id": related_trade_id,
        "requires_review": bool(requires_review),
        "resolved": bool(resolved),
        "extra": extra or {},
    }
    with _lock:
        st = _load()
        entries: List[Dict[str, Any]] = list(st.get("entries") or [])
        entries.append(entry)
        st["entries"] = entries[-2048:]
        st["updated_at"] = entry["timestamp"]
        try:
            _save(st)
        except Exception as exc:
            logger.warning("exception_dashboard save failed: %s", exc)
    _append_log({"event": "add", **entry})
    return entry


def list_open_exceptions() -> List[Dict[str, Any]]:
    st = _load()
    return [e for e in (st.get("entries") or []) if not e.get("resolved")]


def mark_resolved(entry_id: str) -> Dict[str, Any]:
    with _lock:
        st = _load()
        found = False
        for e in st.get("entries") or []:
            if e.get("id") == entry_id:
                e["resolved"] = True
                e["resolved_at"] = datetime.now(timezone.utc).isoformat()
                found = True
                break
        if found:
            try:
                _save(st)
            except Exception as exc:
                logger.warning("exception_dashboard resolve save failed: %s", exc)
    out = {"ok": found, "id": entry_id}
    _append_log({"event": "resolve", **out})
    return out


def dashboard_status() -> Dict[str, Any]:
    st = _load()
    open_e = [e for e in (st.get("entries") or []) if not e.get("resolved")]
    return {
        "ok": True,
        "open_count": len(open_e),
        "total_entries": len(st.get("entries") or []),
        "runtime_root": str(runtime_root()),
        "updated_at": st.get("updated_at"),
    }
