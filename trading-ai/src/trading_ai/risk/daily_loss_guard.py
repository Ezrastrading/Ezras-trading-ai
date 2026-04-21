"""
UTC daily drawdown guard: ``MAX_DAILY_LOSS_PCT`` (e.g. ``0.05`` for 5%).

Halts via ``system_guard.halt_now`` when ``(session_start - current) / session_start >= limit``.
Session state in ``data/control/session_state.json``; resets on UTC day change.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_week() -> str:
    dt = datetime.now(timezone.utc)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _session_path():
    from trading_ai.control.paths import session_state_json_path

    return session_state_json_path()


def load_session_state() -> Dict[str, Any]:
    p = _session_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def save_session_state(d: Dict[str, Any]) -> None:
    try:
        p = _session_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.debug("save_session_state: %s", exc)


def _current_equity() -> float:
    try:
        from trading_ai.shark.state_store import load_capital

        c = load_capital()
        return float(c.current_capital or 0.0)
    except Exception:
        return 0.0


def ensure_session_state() -> Dict[str, Any]:
    """Roll day/week; set ``session_start_equity`` at day boundary."""
    day = _utc_day()
    week = _utc_week()
    st = load_session_state()
    eq = _current_equity()
    changed = False
    if str(st.get("current_day") or "") != day:
        st["current_day"] = day
        st["session_start_equity"] = eq
        changed = True
    if str(st.get("current_week") or "") != week:
        st["current_week"] = week
        st["week_start_equity"] = eq
        changed = True
    if "session_start_equity" not in st:
        st["session_start_equity"] = eq
        changed = True
    if changed:
        save_session_state(st)
    return st


def check_daily_loss_limit() -> Tuple[bool, Optional[str]]:
    """
    If env ``MAX_DAILY_LOSS_PCT`` is set and drawdown from session start exceeds it, halt.

    Returns ``(should_block, reason_if_any)``.
    """
    raw = (os.environ.get("MAX_DAILY_LOSS_PCT") or "").strip()
    if not raw:
        ensure_session_state()
        return False, None
    try:
        limit = float(raw)
    except (TypeError, ValueError):
        return False, None
    if limit <= 0 or limit >= 1.0:
        return False, None

    st = ensure_session_state()
    start = float(st.get("session_start_equity") or 0.0)
    cur = _current_equity()
    if start <= 1e-9:
        return False, None
    dd = (start - cur) / start
    if dd >= limit - 1e-12:
        try:
            from trading_ai.control.alerts import emit_alert

            emit_alert("CRITICAL", f"MAX_DAILY_LOSS_PCT reached drawdown={dd:.4f} limit={limit}")
            from trading_ai.core.system_guard import get_system_guard

            get_system_guard().halt_now("MAX_DAILY_LOSS_PCT_REACHED")
        except Exception as exc:
            logger.debug("daily loss halt: %s", exc)
        return True, "MAX_DAILY_LOSS_PCT_REACHED"
    return False, None
