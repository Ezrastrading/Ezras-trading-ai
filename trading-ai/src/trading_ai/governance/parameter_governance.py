"""
Audit trail for material parameter / threshold changes + deterministic drift detection.

Log: ``{EZRAS_RUNTIME_ROOT}/logs/parameter_governance_log.md``
State: ``{EZRAS_RUNTIME_ROOT}/state/parameter_governance_state.json``
Snapshot: ``{EZRAS_RUNTIME_ROOT}/state/parameter_governance_snapshot.json``
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_STATE_VERSION = 2


def _state_path() -> Path:
    return runtime_root() / "state" / "parameter_governance_state.json"


def _snapshot_path() -> Path:
    return runtime_root() / "state" / "parameter_governance_snapshot.json"


def _log_path() -> Path:
    return runtime_root() / "logs" / "parameter_governance_log.md"


def _default_state() -> Dict[str, Any]:
    return {"version": _STATE_VERSION, "changes": []}


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
        out.setdefault("changes", [])
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
        logger.warning("parameter_governance log append failed: %s", exc)


def validate_parameter_change(
    *,
    parameter_name: str,
    old_value: Any,
    new_value: Any,
    reason: str,
) -> Dict[str, Any]:
    ok = bool(str(reason or "").strip())
    return {"valid": ok, "reason_required": not ok}


def record_parameter_change(
    *,
    parameter_name: str,
    old_value: Any,
    new_value: Any,
    reason: str,
    changed_by: str = "operator",
    impact_area: str = "governance",
    review_required: bool = True,
    source: str = "manual",
) -> Dict[str, Any]:
    v = validate_parameter_change(parameter_name=parameter_name, old_value=old_value, new_value=new_value, reason=reason)
    if not v["valid"]:
        return {"ok": False, "error": "reason_required"}
    row = {
        "id": secrets.token_hex(8),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parameter_name": str(parameter_name),
        "old_value": old_value,
        "new_value": new_value,
        "reason": str(reason),
        "changed_by": str(changed_by),
        "impact_area": str(impact_area),
        "review_required": bool(review_required),
        "source": str(source),
    }
    with _lock:
        st = _load()
        ch: List[Dict[str, Any]] = list(st.get("changes") or [])
        ch.append(row)
        st["changes"] = ch[-2048:]
        try:
            _save(st)
        except Exception as exc:
            logger.warning("parameter_governance save failed: %s", exc)
    _append_log(row)
    return {"ok": True, "record": row}


def get_recent_parameter_changes(*, limit: int = 32) -> List[Dict[str, Any]]:
    st = _load()
    ch = list(st.get("changes") or [])
    return ch[-limit:]


def canonical_tracked_snapshot() -> Dict[str, Any]:
    """
    Single canonical dict of all in-repo tracked operational parameters.
    Used for drift detection vs persisted snapshot.
    """
    snap: Dict[str, Any] = {"schema": "tracked_parameters_v1"}
    try:
        from trading_ai.automation.adaptive_sizing import get_ladder_state

        snap["adaptive_sizing"] = dict((get_ladder_state() or {}).get("ladder") or {})
    except Exception:
        snap["adaptive_sizing"] = {}
    try:
        from trading_ai.risk.hard_lockouts import (
            DEFAULT_ANOMALY_WINDOW_HOURS,
            DEFAULT_DAILY_LOSS_LOCK_PCT,
            DEFAULT_EXECUTION_ANOMALY_COUNT,
            DEFAULT_WEEKLY_DRAWDOWN_LOCK_PCT,
        )

        snap["hard_lockouts"] = {
            "daily_loss_lock_pct": DEFAULT_DAILY_LOSS_LOCK_PCT,
            "weekly_drawdown_lock_pct": DEFAULT_WEEKLY_DRAWDOWN_LOCK_PCT,
            "execution_anomaly_count": DEFAULT_EXECUTION_ANOMALY_COUNT,
            "anomaly_window_hours": DEFAULT_ANOMALY_WINDOW_HOURS,
        }
    except Exception:
        snap["hard_lockouts"] = {}
    try:
        from trading_ai.execution.execution_reconciliation import DEFAULT_PRICE_SLIPPAGE_ABS, DEFAULT_SIZE_REL_TOLERANCE

        snap["execution_reconciliation"] = {
            "size_rel_tolerance": DEFAULT_SIZE_REL_TOLERANCE,
            "price_slippage_abs": DEFAULT_PRICE_SLIPPAGE_ABS,
        }
    except Exception:
        snap["execution_reconciliation"] = {}
    snap["risk_bucket"] = {"state_file": "risk_state.json"}
    return snap


def _snapshot_fingerprint(snap: Dict[str, Any]) -> str:
    canonical = json.dumps(snap, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_snapshot_file() -> Optional[Dict[str, Any]]:
    p = _snapshot_path()
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_snapshot_file(snap: Dict[str, Any], fp: str) -> None:
    p = _snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fp,
        "snapshot": snap,
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def check_tracked_parameter_drift(*, trigger: str = "status_check") -> Dict[str, Any]:
    """
    Compare current :func:`canonical_tracked_snapshot` to persisted fingerprint.
    On any change, append an automatic governance record (auditable, deterministic).
    """
    current = canonical_tracked_snapshot()
    fp = _snapshot_fingerprint(current)
    prior = _load_snapshot_file()
    out: Dict[str, Any] = {
        "drift_detected": False,
        "current_fingerprint": fp,
        "prior_fingerprint": (prior or {}).get("fingerprint"),
        "trigger": trigger,
        "auto_records": [],
    }
    if prior is None:
        _write_snapshot_file(current, fp)
        out["note"] = "initial_snapshot_written"
        return out

    if prior.get("fingerprint") == fp:
        return out

    out["drift_detected"] = True
    rec = record_parameter_change(
        parameter_name="tracked_parameter_snapshot",
        old_value=prior.get("fingerprint"),
        new_value=fp,
        reason=f"automatic_drift_detection:{trigger}",
        changed_by="system",
        impact_area="governance",
        review_required=True,
        source="automatic_drift",
    )
    if rec.get("ok"):
        out["auto_records"].append(rec.get("record"))
    _write_snapshot_file(current, fp)
    return out


def snapshot_tracked_parameters() -> Dict[str, Any]:
    """CLI / gap-check: current tracked values + fingerprint."""
    snap = canonical_tracked_snapshot()
    return {
        "tracked": snap,
        "fingerprint": _snapshot_fingerprint(snap),
        "snapshot_path": str(_snapshot_path()),
    }
