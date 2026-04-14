"""Kalshi-only position bounds and open-position caps (env-driven for Railway)."""

from __future__ import annotations

import os


def kalshi_max_open_positions_from_env() -> int:
    """Railway-safe: unset or non-positive env must not cap at 0 (would block all trades)."""
    raw = (os.environ.get("KALSHI_MAX_OPEN_POSITIONS") or "12").strip() or "12"
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return 12
    if n <= 0:
        return 12
    return max(1, min(n, 500))


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
    """HV near-resolution: max simultaneous Kalshi opens (default 8, capped by general env)."""
    raw = (os.environ.get("KALSHI_HV_MAX_OPEN_POSITIONS") or "8").strip() or "8"
    try:
        hv = int(float(raw))
    except (TypeError, ValueError):
        hv = 8
    if hv <= 0:
        hv = 8
    hv = max(1, min(50, hv))
    return min(hv, kalshi_max_open_positions_from_env())


def kalshi_fetch_top_n() -> int:
    """Max Kalshi markets returned from active-pool merge per scan (default 200)."""
    raw = (os.environ.get("KALSHI_FETCH_TOP_N") or "200").strip() or "200"
    try:
        return max(20, min(2000, int(float(raw))))
    except (TypeError, ValueError):
        return 200


def kalshi_series_merge_cap() -> int:
    """Per-series fetch cap when building the active pool (default 120)."""
    raw = (os.environ.get("KALSHI_SERIES_MERGE_CAP") or "120").strip() or "120"
    try:
        return max(20, min(500, int(float(raw))))
    except (TypeError, ValueError):
        return 120


def kalshi_markets_api_batch_limit() -> int:
    """Generic ``GET /markets`` batch size when augmenting the merge (default 200)."""
    raw = (os.environ.get("KALSHI_MARKETS_API_BATCH_LIMIT") or "200").strip() or "200"
    try:
        return max(50, min(1000, int(float(raw))))
    except (TypeError, ValueError):
        return 200


def kalshi_open_fallback_slice() -> int:
    """After open fallback, max rows kept for mapping (default 200)."""
    raw = (os.environ.get("KALSHI_OPEN_FALLBACK_SLICE") or "200").strip() or "200"
    try:
        return max(20, min(2000, int(float(raw))))
    except (TypeError, ValueError):
        return 200


def kalshi_fetch_markets_open_limit() -> int:
    """``fetch_markets_open`` list size when active-pool merge is empty (default 500)."""
    raw = (os.environ.get("KALSHI_FETCH_MARKETS_OPEN_LIMIT") or "500").strip() or "500"
    try:
        return max(100, min(2000, int(float(raw))))
    except (TypeError, ValueError):
        return 500


def kalshi_open_notional_usd() -> float:
    from trading_ai.shark.state_store import load_positions

    return sum(
        float(p.get("notional_usd", 0) or 0)
        for p in (load_positions().get("open_positions") or [])
        if str(p.get("outlet") or "").lower() == "kalshi"
    )
