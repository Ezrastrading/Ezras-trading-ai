"""
Automation heartbeat registry: last-seen timestamps per critical component.

Call ``record_heartbeat`` from scheduled/manual entrypoints; status is inspectable JSON.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

STATE_FILE = "automation_heartbeat_state.json"
LOG_FILE = "automation_heartbeat_log.md"

# component_id -> expected max gap (minutes) before STALE
# Tracked components only (each must have a real emitter or activation-seed path).
DEFAULT_EXPECTED_INTERVALS: Dict[str, float] = {
    "morning_cycle": 36 * 60,
    "evening_cycle": 36 * 60,
    "post_trade": 24 * 60,
    "truth_sync": 7 * 24 * 60,
    "memo_generation": 48 * 60,
    "pipeline_schedule": 24 * 60,
}


def _state_path() -> Path:
    return runtime_root() / "state" / STATE_FILE


def _log_path() -> Path:
    return runtime_root() / "logs" / LOG_FILE


def _load() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return {"version": 1, "heartbeats": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"version": 1, "heartbeats": {}}
        raw.setdefault("heartbeats", {})
        return raw
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "heartbeats": {}}


def _save(data: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _append_md(msg: str) -> None:
    try:
        lp = _log_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        with open(lp, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError as exc:
        logger.warning("automation_heartbeat log: %s", exc)


def record_heartbeat(
    component: str,
    *,
    ok: bool = True,
    note: str = "",
    expected_interval_minutes: Optional[float] = None,
) -> None:
    """Record a successful (or failed) run for a named component."""
    ts = datetime.now(timezone.utc).isoformat()
    exp = expected_interval_minutes
    if exp is None:
        exp = DEFAULT_EXPECTED_INTERVALS.get(component, 24 * 60)
    row = {
        "last_seen_at": ts,
        "status": "OK" if ok else "FAILED",
        "expected_interval_minutes": exp,
        "note": note,
    }
    with _lock:
        st = _load()
        st["heartbeats"][component] = row
        _save(st)
    _append_md(f"- {ts} | {component} | {row['status']} | {note}")


def _classify(last_iso: str, expected_min: float) -> str:
    try:
        last = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "UNKNOWN"
    now = datetime.now(timezone.utc)
    delta_min = (now - last).total_seconds() / 60.0
    if delta_min <= expected_min * 1.2:
        return "OK"
    if delta_min <= expected_min * 2:
        return "STALE"
    return "STALE"


def heartbeat_status() -> Dict[str, Any]:
    """Full heartbeat table + aggregate health.

    UNKNOWN (never recorded) does not degrade overall health — only STALE/FAILED do.
    Callers use activation-seed to populate rows before expecting full OK coverage.
    """
    st = _load()
    hb = st.get("heartbeats") or {}
    rows: List[Dict[str, Any]] = []
    unknown: List[str] = []
    degraded_reasons: List[str] = []
    for comp, exp_min in DEFAULT_EXPECTED_INTERVALS.items():
        data = hb.get(comp)
        if not data:
            rows.append(
                {
                    "component": comp,
                    "last_seen_at": None,
                    "status": "UNKNOWN",
                    "expected_interval_minutes": exp_min,
                    "note": "never_recorded",
                }
            )
            unknown.append(comp)
            continue
        last = str(data.get("last_seen_at", ""))
        exp = float(data.get("expected_interval_minutes", exp_min))
        stat = str(data.get("status", "OK"))
        if stat == "FAILED":
            cls = "FAILED"
        else:
            cls = _classify(last, exp)
        if cls == "STALE":
            degraded_reasons.append(f"stale:{comp}")
        if cls == "FAILED":
            degraded_reasons.append(f"failed:{comp}")
        rows.append(
            {
                "component": comp,
                "last_seen_at": last,
                "status": cls,
                "expected_interval_minutes": exp,
                "note": data.get("note", ""),
            }
        )

    overall = "healthy" if not degraded_reasons else "degraded"
    return {
        "components": rows,
        "overall": overall,
        "unknown_components": unknown,
        "degraded_reasons": degraded_reasons,
        "stale_or_unknown_components": unknown + [x.split(":", 1)[-1] for x in degraded_reasons if x.startswith("stale:")],
        "state_path": str(_state_path()),
    }
