"""
Hard capital lockouts — absolute blocks independent of bucket NORMAL/REDUCED.

State: ``{EZRAS_RUNTIME_ROOT}/state/hard_lockout_state.json``
Log: ``{EZRAS_RUNTIME_ROOT}/logs/hard_lockout_log.md``
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import risk_state_path, runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 1

DEFAULT_DAILY_LOSS_LOCK_PCT = 3.0
DEFAULT_WEEKLY_DRAWDOWN_LOCK_PCT = 8.0
DEFAULT_EXECUTION_ANOMALY_COUNT = 3
DEFAULT_ANOMALY_WINDOW_HOURS = 24


def _state_path() -> Path:
    return runtime_root() / "state" / "hard_lockout_state.json"


def _log_path() -> Path:
    return runtime_root() / "logs" / "hard_lockout_log.md"


def _governance_override_log_path() -> Path:
    return runtime_root() / "logs" / "hard_lockout_governance_log.md"


def _default_state() -> Dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "daily_lockout_active": False,
        "weekly_lockout_active": False,
        "execution_lockout_active": False,
        "effective_lockout": False,
        "reasons": [],
        "daily_loss_percent": 0.0,
        "weekly_drawdown_percent": 0.0,
        "execution_anomaly_count_1d": 0,
        "updated_at": None,
        "trading_day_utc": None,
        "week_id_utc": None,
        "equity_at_day_start": None,
        "equity_week_peak": None,
        "weekly_manual_clear_pending": False,
        "execution_anomaly_timestamps": [],
        "override_history": [],
    }


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
        out.setdefault("reasons", [])
        out.setdefault("execution_anomaly_timestamps", [])
        out.setdefault("override_history", [])
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


def _append_governance_log(row: Dict[str, Any]) -> None:
    try:
        _governance_override_log_path().parent.mkdir(parents=True, exist_ok=True)
        ts = row.get("timestamp") or datetime.now(timezone.utc).isoformat()
        block = f"\n## {ts}\n\n```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        with open(_governance_override_log_path(), "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("hard_lockout governance log append failed: %s", exc)


def _append_log(row: Dict[str, Any]) -> None:
    try:
        _log_path().parent.mkdir(parents=True, exist_ok=True)
        ts = row.get("timestamp") or datetime.now(timezone.utc).isoformat()
        block = f"\n## {ts}\n\n```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("hard_lockout log append failed: %s", exc)


def _utc_day(ts: Optional[datetime] = None) -> str:
    t = ts or datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%d")


def _utc_week_id(ts: Optional[datetime] = None) -> str:
    t = ts or datetime.now(timezone.utc)
    iso = t.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _read_equity() -> float:
    try:
        p = risk_state_path()
        if not p.is_file():
            return 100.0
        raw = json.loads(p.read_text(encoding="utf-8"))
        return float(raw.get("equity_index") or 100.0)
    except Exception:
        return 100.0


def _recompute_effective(st: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    if st.get("daily_lockout_active"):
        reasons.append("daily_loss_lockout")
    if st.get("weekly_lockout_active"):
        reasons.append("weekly_drawdown_lockout")
    if st.get("execution_lockout_active"):
        reasons.append("repeated_execution_anomaly_lockout")
    st["effective_lockout"] = len(reasons) > 0
    st["reasons"] = reasons
    st["updated_at"] = datetime.now(timezone.utc).isoformat()
    return st


def clear_daily_lockout_if_new_day(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    with _lock:
        st = _load()
        day = _utc_day(now)
        if st.get("trading_day_utc") != day:
            st["daily_lockout_active"] = False
            st["trading_day_utc"] = day
            st["equity_at_day_start"] = _read_equity()
            st = _recompute_effective(st)
            try:
                _save(st)
            except Exception as exc:
                logger.warning("hard_lockout save failed: %s", exc)
    _append_log({"event": "clear_daily_if_new_day", "trading_day": _utc_day(now)})
    return get_effective_lockout()


def apply_weekly_period_rollover_if_needed(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    ISO week change clears **weekly** lockout automatically (new period reset).
    This is the only implicit weekly clear — no silent mid-week clear.
    """
    with _lock:
        st = _load()
        wid = _utc_week_id(now)
        if st.get("week_id_utc") != wid:
            st["weekly_lockout_active"] = False
            st["week_id_utc"] = wid
            st["equity_week_peak"] = _read_equity()
            st = _recompute_effective(st)
            try:
                _save(st)
            except Exception as exc:
                logger.warning("hard_lockout save failed: %s", exc)
            _append_log({"event": "weekly_period_rollover", "week": wid})
    return get_effective_lockout()


def clear_weekly_lockout_if_new_period_or_manual_override(
    *,
    manual: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Backward compat: non-manual path delegates to period rollover only."""
    if manual:
        raise RuntimeError("use_clear_weekly_lockout_manual_with_actor_and_reason")
    return apply_weekly_period_rollover_if_needed(now=now)


def clear_weekly_lockout_manual(
    *,
    actor: str,
    reason: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Explicit operator override — requires actor + reason; persisted and governance-logged.
    Weekly lockout cannot clear mid-week without this or ``apply_weekly_period_rollover_if_needed``.
    """
    a = str(actor or "").strip()
    r = str(reason or "").strip()
    if not a or not r:
        return {"ok": False, "error": "actor_and_reason_required", "effective_lockout": get_effective_lockout().get("effective_lockout")}

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "weekly_lockout_clear_manual",
        "actor": a,
        "reason": r,
        "week_id": _utc_week_id(now),
    }
    with _lock:
        st = _load()
        st["weekly_lockout_active"] = False
        st["weekly_manual_clear_pending"] = True
        hist: List[Dict[str, Any]] = list(st.get("override_history") or [])
        hist.append(entry)
        st["override_history"] = hist[-256:]
        st = _recompute_effective(st)
        try:
            _save(st)
        except Exception as exc:
            logger.warning("hard_lockout save failed: %s", exc)

    _append_log({"event": "clear_weekly_manual", **entry})
    _append_governance_log({"event": "lockout_override", **entry})
    try:
        from trading_ai.governance.parameter_governance import record_parameter_change

        record_parameter_change(
            parameter_name="hard_lockout.weekly_cleared_manual",
            old_value="locked",
            new_value="cleared",
            reason=f"manual_override:{r}",
            changed_by=a,
            impact_area="risk",
            review_required=True,
            source="lockout_override",
        )
    except Exception as exc:
        logger.warning("governance record for lockout override failed: %s", exc)

    return {"ok": True, **get_effective_lockout()}


def clear_daily_lockout_manual(*, actor: str, reason: str) -> Dict[str, Any]:
    a = str(actor or "").strip()
    r = str(reason or "").strip()
    if not a or not r:
        return {"ok": False, "error": "actor_and_reason_required"}
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "daily_lockout_clear_manual",
        "actor": a,
        "reason": r,
    }
    with _lock:
        st = _load()
        st["daily_lockout_active"] = False
        hist: List[Dict[str, Any]] = list(st.get("override_history") or [])
        hist.append(entry)
        st["override_history"] = hist[-256:]
        st = _recompute_effective(st)
        try:
            _save(st)
        except Exception as exc:
            logger.warning("hard_lockout save failed: %s", exc)
    _append_log({"event": "clear_daily_manual", **entry})
    _append_governance_log({"event": "lockout_override", **entry})
    try:
        from trading_ai.governance.parameter_governance import record_parameter_change

        record_parameter_change(
            parameter_name="hard_lockout.daily_cleared_manual",
            old_value="locked",
            new_value="cleared",
            reason=f"manual_override:{r}",
            changed_by=a,
            impact_area="risk",
            review_required=True,
            source="lockout_override",
        )
    except Exception as exc:
        logger.warning("governance record for daily lockout override failed: %s", exc)
    return {"ok": True, **get_effective_lockout()}


def update_lockout_state_from_closed_trade(
    trade: Dict[str, Any],
    *,
    daily_loss_lock_pct: float = DEFAULT_DAILY_LOSS_LOCK_PCT,
    weekly_dd_lock_pct: float = DEFAULT_WEEKLY_DRAWDOWN_LOCK_PCT,
) -> Dict[str, Any]:
    """Update daily / weekly metrics after a closed trade (risk_state already advanced)."""
    clear_daily_lockout_if_new_day()
    apply_weekly_period_rollover_if_needed()
    eq = _read_equity()
    with _lock:
        st = _load()
        day = _utc_day()
        if st.get("trading_day_utc") != day:
            st["trading_day_utc"] = day
            st["equity_at_day_start"] = eq
        start = float(st.get("equity_at_day_start") or eq)
        if start <= 0:
            start = eq
        day_change_pct = (eq / start - 1.0) * 100.0
        st["daily_loss_percent"] = min(0.0, day_change_pct)

        wid = _utc_week_id()
        if st.get("week_id_utc") != wid:
            st["week_id_utc"] = wid
            st["equity_week_peak"] = eq
        peak = float(st.get("equity_week_peak") or eq)
        peak = max(peak, eq)
        st["equity_week_peak"] = peak
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        st["weekly_drawdown_percent"] = dd

        if day_change_pct <= -daily_loss_lock_pct:
            st["daily_lockout_active"] = True
        if dd >= weekly_dd_lock_pct:
            st["weekly_lockout_active"] = True

        st = _recompute_effective(st)
        try:
            _save(st)
        except Exception as exc:
            logger.warning("hard_lockout save failed: %s", exc)

    if _load().get("effective_lockout"):
        try:
            from trading_ai.ops.exception_dashboard import add_exception_event

            add_exception_event(
                category="lockout_active",
                message="Hard lockout engaged after close: " + "; ".join(_load().get("reasons") or []),
                severity="CRITICAL",
                related_trade_id=str(trade.get("trade_id") or ""),
                requires_review=True,
            )
        except Exception as exc:
            logger.warning("exception dashboard lockout hook failed: %s", exc)

    _append_log({"event": "closed_trade_update", "trade_id": trade.get("trade_id")})
    return get_effective_lockout()


def update_lockout_state_from_execution_reconciliation(
    reconciliation: Dict[str, Any],
    *,
    anomaly_threshold: int = DEFAULT_EXECUTION_ANOMALY_COUNT,
    window_hours: float = DEFAULT_ANOMALY_WINDOW_HOURS,
) -> Dict[str, Any]:
    """Increment rolling anomaly counter on material reconciliation issues."""
    verdict = str(reconciliation.get("execution_quality_verdict") or "")
    material = verdict in ("SIZE_DRIFT", "PARTIAL_FILL", "PRICE_DRIFT", "DISCREPANCY")
    if not material:
        return get_effective_lockout()

    now = datetime.now(timezone.utc)
    ts_list: List[str] = list(_load().get("execution_anomaly_timestamps") or [])
    ts_list.append(now.isoformat())
    cutoff = now - timedelta(hours=window_hours)
    pruned = []
    for s in ts_list:
        try:
            t = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t >= cutoff:
                pruned.append(s)
        except Exception:
            continue

    with _lock:
        st = _load()
        st["execution_anomaly_timestamps"] = pruned
        st["execution_anomaly_count_1d"] = len(pruned)
        if len(pruned) >= anomaly_threshold:
            st["execution_lockout_active"] = True
        st = _recompute_effective(st)
        try:
            _save(st)
        except Exception as exc:
            logger.warning("hard_lockout save failed: %s", exc)

    st2 = get_effective_lockout()
    if st2.get("execution_lockout_active"):
        try:
            from trading_ai.ops.exception_dashboard import add_exception_event

            add_exception_event(
                category="execution_anomaly",
                message="Execution anomaly lockout threshold reached",
                severity="CRITICAL",
                related_trade_id=str(reconciliation.get("trade_id") or ""),
                requires_review=True,
            )
        except Exception as exc:
            logger.warning("exception dashboard anomaly hook failed: %s", exc)
    _append_log({"event": "execution_reconciliation", "verdict": verdict})
    return st2


def get_effective_lockout() -> Dict[str, Any]:
    st = _load()
    return {
        "daily_lockout_active": bool(st.get("daily_lockout_active")),
        "weekly_lockout_active": bool(st.get("weekly_lockout_active")),
        "execution_lockout_active": bool(st.get("execution_lockout_active")),
        "effective_lockout": bool(st.get("effective_lockout")),
        "reasons": list(st.get("reasons") or []),
        "daily_loss_percent": float(st.get("daily_loss_percent") or 0.0),
        "weekly_drawdown_percent": float(st.get("weekly_drawdown_percent") or 0.0),
        "execution_anomaly_count_1d": int(st.get("execution_anomaly_count_1d") or 0),
        "updated_at": st.get("updated_at"),
        "runtime_root": str(runtime_root()),
    }


def can_open_new_trade() -> Dict[str, Any]:
    clear_daily_lockout_if_new_day()
    apply_weekly_period_rollover_if_needed()
    st = get_effective_lockout()
    ok = not st["effective_lockout"]
    return {
        "allowed": ok,
        "effective_lockout": st["effective_lockout"],
        "reasons": st["reasons"],
    }


def simulate_daily_loss(pct: float) -> Dict[str, Any]:
    """Force daily lockout edge for operator drill (writes state)."""
    with _lock:
        st = _load()
        st["daily_lockout_active"] = float(pct) >= DEFAULT_DAILY_LOSS_LOCK_PCT
        st["daily_loss_percent"] = -abs(float(pct))
        st = _recompute_effective(st)
        try:
            _save(st)
        except Exception as exc:
            logger.warning("hard_lockout save failed: %s", exc)
    _append_log({"event": "simulate_daily_loss", "pct": pct})
    return get_effective_lockout()


def simulate_weekly_drawdown(pct: float) -> Dict[str, Any]:
    with _lock:
        st = _load()
        st["weekly_drawdown_percent"] = float(pct)
        st["weekly_lockout_active"] = float(pct) >= DEFAULT_WEEKLY_DRAWDOWN_LOCK_PCT
        st = _recompute_effective(st)
        try:
            _save(st)
        except Exception as exc:
            logger.warning("hard_lockout save failed: %s", exc)
    _append_log({"event": "simulate_weekly_dd", "pct": pct})
    return get_effective_lockout()


def clear_daily_override() -> Dict[str, Any]:
    return clear_daily_lockout_manual(actor="cli_legacy", reason="clear_daily_override_backward_compat")


def clear_weekly_override() -> Dict[str, Any]:
    return clear_weekly_lockout_manual(actor="cli_legacy", reason="clear_weekly_override_backward_compat")
