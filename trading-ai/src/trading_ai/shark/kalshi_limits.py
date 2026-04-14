"""Kalshi-only position bounds and open-position caps (env-driven for Railway)."""

from __future__ import annotations

import os


def kalshi_max_open_positions_from_env() -> int:
    raw = (os.environ.get("KALSHI_MAX_OPEN_POSITIONS") or "5").strip() or "5"
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 5


def kalshi_min_position_usd() -> float:
    raw = (os.environ.get("KALSHI_MIN_POSITION_USD") or "1").strip() or "1"
    try:
        return max(0.5, float(raw))
    except (TypeError, ValueError):
        return 1.0


def kalshi_max_position_usd() -> float:
    raw = (os.environ.get("KALSHI_MAX_POSITION_USD") or "5").strip() or "5"
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 5.0


def kalshi_notional_bounds_usd() -> tuple[float, float]:
    lo, hi = kalshi_min_position_usd(), kalshi_max_position_usd()
    if hi < lo:
        return hi, lo
    return lo, hi


def count_kalshi_open_positions() -> int:
    from trading_ai.shark.state_store import load_positions

    data = load_positions()
    return sum(
        1
        for p in (data.get("open_positions") or [])
        if str(p.get("outlet") or "").lower() == "kalshi"
    )


def kalshi_hv_max_open_positions() -> int:
    """HV near-resolution: max simultaneous Kalshi opens (default 3, capped by general env)."""
    raw = (os.environ.get("KALSHI_HV_MAX_OPEN_POSITIONS") or "3").strip() or "3"
    try:
        hv = max(1, min(10, int(float(raw))))
    except (TypeError, ValueError):
        hv = 3
    return min(hv, kalshi_max_open_positions_from_env())


def kalshi_open_notional_usd() -> float:
    from trading_ai.shark.state_store import load_positions

    return sum(
        float(p.get("notional_usd", 0) or 0)
        for p in (load_positions().get("open_positions") or [])
        if str(p.get("outlet") or "").lower() == "kalshi"
    )
