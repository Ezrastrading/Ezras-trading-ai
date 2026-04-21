"""Kalshi time-to-resolution caps (no multi-day / far-dated trades)."""

from __future__ import annotations

import os

from trading_ai.shark.models import MarketSnapshot


def kalshi_max_ttr_seconds() -> float:
    """Default 5400s (90m). ``KALSHI_MAX_TTR_SECONDS``."""
    raw = (os.environ.get("KALSHI_MAX_TTR_SECONDS") or "5400").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 5400.0


def kalshi_snapshot_over_max_ttr(m: MarketSnapshot) -> bool:
    """True if Kalshi market TTR exceeds max (or missing / non-positive)."""
    if (m.outlet or "").lower() != "kalshi":
        return False
    ttr = float(m.time_to_resolution_seconds or 0.0)
    if ttr <= 0:
        return True
    return ttr > kalshi_max_ttr_seconds()
