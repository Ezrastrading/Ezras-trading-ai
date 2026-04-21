"""Rate + expectancy guard — blocks new entries briefly when churning into negative recent PnL."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.control.paths import control_data_dir

logger = logging.getLogger(__name__)

_STATE_NAME = "overtrading_guard_state.json"
_WINDOW_SEC = 300.0
_COOLDOWN_SEC = 60.0
_TRADE_BURST = 5
_LAST_N = 5


def _state_path() -> Path:
    return control_data_dir() / _STATE_NAME


def _parse_ts_close(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(st: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    tmp.replace(p)


def overtrading_cooldown_active() -> Tuple[bool, float]:
    st = _load_state()
    until = float(st.get("cooldown_until") or 0.0)
    now = time.time()
    if until > now:
        return True, until
    return False, 0.0


def refresh_overtrading_after_close(events: List[Dict[str, Any]]) -> None:
    """
    After a closed trade, recompute burst rate and last-N net PnL; arm cooldown if rules hit.
    """
    if (os.environ.get("EZRAS_OVERTRADING_GUARD") or "1").strip().lower() in ("0", "false", "no"):
        return
    now = time.time()
    recent_ts: List[float] = []
    for e in events:
        ts = _parse_ts_close(e.get("timestamp_close"))
        if ts is not None and now - ts <= _WINDOW_SEC:
            recent_ts.append(ts)
    trades_last_5m = len(recent_ts)
    tail = events[-_LAST_N:] if len(events) >= _LAST_N else list(events)
    pnls = [float(x.get("net_pnl") or x.get("net_pnl_usd") or 0.0) for x in tail if isinstance(x, dict)]
    avg_last = sum(pnls) / len(pnls) if pnls else 0.0

    st = _load_state()
    if trades_last_5m > _TRADE_BURST and avg_last < 0.0:
        until = now + _COOLDOWN_SEC
        st["cooldown_until"] = until
        st["last_reason"] = {
            "trades_last_5_minutes": trades_last_5m,
            "avg_net_pnl_last_5": avg_last,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        _save_state(st)
        try:
            from trading_ai.control.alerts import emit_alert

            emit_alert("WARNING", "Overtrading detected")
        except Exception as exc:
            logger.debug("overtrading alert skipped: %s", exc)
    else:
        # Clear stale cooldown if past
        until = float(st.get("cooldown_until") or 0.0)
        if until <= now and st.get("cooldown_until"):
            st.pop("cooldown_until", None)
            _save_state(st)


def overtrading_should_block() -> Tuple[bool, str]:
    active, _ = overtrading_cooldown_active()
    if active:
        return True, "overtrading_cooldown"
    return False, ""
