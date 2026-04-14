"""Cancel Kalshi resting limit orders that never filled — frees capital and position slots."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _parse_order_created_ts(order: Dict[str, Any]) -> Optional[float]:
    raw = order.get("created_time") or order.get("created_at")
    if not raw or not isinstance(raw, str):
        return None
    t = raw.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _order_fill_count(order: Dict[str, Any]) -> float:
    fp = order.get("fill_count_fp")
    if fp is not None:
        try:
            return float(str(fp).strip())
        except ValueError:
            pass
    for k in ("filled_count", "fill_count"):
        v = order.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def run_kalshi_stale_resting_order_sweep() -> None:
    """List resting orders; cancel any with zero fills older than the configured age (default ~10s)."""
    if (os.environ.get("KALSHI_STALE_ORDER_SWEEP_ENABLED") or "true").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return
    raw = (os.environ.get("KALSHI_STALE_ORDER_MINUTES") or "0.17").strip() or "0.17"
    try:
        stale_sec = max(1.0, float(raw) * 60.0)
    except ValueError:
        stale_sec = 10.0

    from trading_ai.shark.outlets.kalshi import KalshiClient

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        return
    try:
        orders = client.list_resting_orders()
    except Exception as exc:
        logger.warning("Kalshi stale order sweep: list resting orders failed: %s", exc)
        return

    now = time.time()
    for o in orders:
        if _order_fill_count(o) > 0:
            continue
        oid = str(o.get("order_id") or "").strip()
        ticker = str(o.get("ticker") or "").strip() or "?"
        ts = _parse_order_created_ts(o)
        if ts is None:
            continue
        age_sec = now - ts
        if age_sec <= stale_sec:
            continue
        try:
            client.cancel_order(oid)
        except Exception as exc:
            logger.warning("Kalshi stale order sweep: cancel failed %s %s: %s", ticker, oid, exc)
            continue
        logger.info("Cancelled stale resting order: [%s] age=%.1fs", ticker, age_sec)
