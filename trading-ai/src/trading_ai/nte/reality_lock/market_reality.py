"""Spread, liquidity, and slippage gates before any trade."""

from __future__ import annotations

import os
from typing import Any, Optional


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def spread_bps(bid: float, ask: float) -> float:
    mid = (float(bid) + float(ask)) / 2.0
    if mid <= 0:
        return 1e9
    return (float(ask) - float(bid)) / mid * 10000.0


def volume_proxy_1m_usd(quote_volume_24h: float) -> float:
    """Rough 1m USD volume from 24h quote volume (no L2 tape in NTE)."""
    return max(0.0, float(quote_volume_24h)) / 1440.0


def check_market_reality_pre_trade(
    *,
    bid: float,
    ask: float,
    quote_volume_24h: float,
    net_edge_bps: float,
    spread_bps_est: float,
) -> tuple[bool, str]:
    """
    Block when spread too wide, 1m liquidity proxy too low, or slippage estimate eats the edge.

    Tunable via ``REALITY_LOCK_MAX_SPREAD_BPS``, ``REALITY_LOCK_MIN_VOLUME_1M_USD``,
    ``REALITY_LOCK_SLIPPAGE_VS_EDGE_MULT`` (slippage estimate = spread_bps/2 must stay below edge).
    """
    max_sp = _env_float("REALITY_LOCK_MAX_SPREAD_BPS", 50.0)
    min_v = _env_float("REALITY_LOCK_MIN_VOLUME_1M_USD", 1.0)
    slip_mult = _env_float("REALITY_LOCK_SLIPPAGE_VS_EDGE_MULT", 1.0)

    sp = spread_bps(bid, ask)
    if sp > max_sp:
        return False, f"spread_bps>{max_sp}"

    v1 = volume_proxy_1m_usd(quote_volume_24h)
    if v1 < min_v:
        return False, f"volume_1m_proxy<{min_v}"

    slip_est_bps = float(spread_bps_est) * 0.5
    edge = float(net_edge_bps)
    if edge <= 0:
        return False, "non_positive_net_edge_bps"
    if slip_est_bps * slip_mult > edge:
        return False, "slippage_estimate_bps>EXPECTED_EDGE_BPS"

    return True, "ok"


def check_order_timestamp_fresh(order: Any, *, max_age_sec: float) -> bool:
    """Reject obviously stale order payloads (wall-clock skew)."""
    if not isinstance(order, dict):
        return True
    ts = order.get("created_time") or order.get("last_fill_time") or order.get("time")
    if not ts:
        return True
    try:
        from datetime import datetime, timezone

        if isinstance(ts, (int, float)):
            tsec = float(ts) if float(ts) > 1e12 else float(ts)
        else:
            s = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            tsec = dt.timestamp()
        import time

        return abs(time.time() - tsec) <= max_age_sec
    except Exception:
        return True
