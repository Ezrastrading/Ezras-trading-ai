"""Roll net PnL from trades + update global daily/weekly/monthly summaries."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from trading_ai.global_layer.avenue_truth_contract import normalize_avenue_key
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

logger = logging.getLogger(__name__)


def _parse_ts(t: Any) -> float:
    if t is None:
        return 0.0
    if isinstance(t, (int, float)):
        return float(t)
    s = str(t)
    try:
        return float(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0.0


def aggregate_from_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bucket by calendar day (UTC) for summaries."""
    now = time.time()
    day_cut = now - 86400
    week_cut = now - 7 * 86400
    month_cut = now - 30 * 86400
    d_net = defaultdict(float)
    w_net = defaultdict(float)
    m_net = defaultdict(float)
    by_avenue: Dict[str, float] = defaultdict(float)
    for t in trades:
        raw = t.get("net_pnl_usd")
        if raw is None:
            raw = t.get("net_pnl")
        try:
            net = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            net = 0.0
        av = normalize_avenue_key(t.get("avenue") or t.get("avenue_name") or t.get("avenue_id") or "coinbase")
        by_avenue[av] += net
        ts = _parse_ts(t.get("logged_at") or t.get("exit_time") or t.get("ts"))
        if ts >= day_cut:
            day = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
            d_net[day] += net
        if ts >= week_cut:
            w_net["rolling_7d"] += net
        if ts >= month_cut:
            m_net["rolling_30d"] += net
    return {
        "daily_buckets": dict(d_net),
        "rolling_7d_net_usd": float(w_net.get("rolling_7d", 0.0)),
        "rolling_30d_net_usd": float(m_net.get("rolling_30d", 0.0)),
        "by_avenue": dict(by_avenue),
    }


def refresh_global_pnl_files(store: GlobalMemoryStore, trades: List[Dict[str, Any]]) -> None:
    agg = aggregate_from_trades(trades)
    daily = store.load_json("daily_pnl_summary.json")
    daily["period_net_usd"] = sum(agg["daily_buckets"].values()) if agg["daily_buckets"] else 0.0
    daily["by_avenue"] = agg["by_avenue"]
    daily["trade_count"] = len(trades)
    daily["notes"] = "from federated trade list (nte memory + databank enrichment)"
    store.save_json("daily_pnl_summary.json", daily)

    w = store.load_json("weekly_pnl_summary.json")
    w["period_net_usd"] = agg["rolling_7d_net_usd"]
    w["by_avenue"] = agg["by_avenue"]
    w["trade_count"] = len([t for t in trades if _parse_ts(t.get("logged_at")) >= time.time() - 7 * 86400])
    store.save_json("weekly_pnl_summary.json", w)

    m = store.load_json("monthly_pnl_summary.json")
    m["period_net_usd"] = agg["rolling_30d_net_usd"]
    m["by_avenue"] = agg["by_avenue"]
    store.save_json("monthly_pnl_summary.json", m)
