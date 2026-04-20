"""Time parsing for trade rows — standalone module (avoids package import cycles)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def parse_trade_ts(trade: Dict[str, Any]) -> Optional[float]:
    """Best-effort Unix seconds UTC for a closed trade row."""
    for k in ("closed_at", "exit_ts", "logged_at", "ts", "timestamp"):
        v = trade.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            x = float(v)
            if x > 1e12:
                x = x / 1000.0
            return x
        if isinstance(v, str):
            s = v.strip()
            if not s:
                continue
            try:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except (TypeError, ValueError):
                continue
    return None


def iso_week_id(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def last_n_iso_week_ids(now_ts: float, n: int) -> List[str]:
    dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    out: List[str] = []
    for i in range(n):
        d = dt - timedelta(days=7 * i)
        y, w, _ = d.isocalendar()
        out.append(f"{y}-W{w:02d}")
    return out
