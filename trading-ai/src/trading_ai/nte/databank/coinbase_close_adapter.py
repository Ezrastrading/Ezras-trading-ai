"""Map NTE Coinbase close artifacts to Trade Intelligence databank payloads."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


def _iso_from_unix(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def coinbase_nt_close_to_databank_raw(
    pos: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    exit_reason: str,
) -> Dict[str, Any]:
    """
    Build a ``process_closed_trade`` payload from NTE position + learning record.

    ``trade_id`` is the stable NTE position id so memory and databank correlate.
    """
    pos_id = str(pos.get("id") or record.get("trade_id") or "")
    if not pos_id:
        raise ValueError("coinbase close: missing position id for trade_id")

    opened = float(pos.get("opened_ts") or 0)
    now = time.time()
    dur = float(record.get("duration_sec") or 0)
    closed = opened + dur if opened > 0 else now
    if opened <= 0:
        opened = max(0.0, closed - max(dur, 1.0))

    strat = str(pos.get("strategy") or record.get("setup_type") or "unknown")
    st_lower = strat.lower()
    if "continuation" in st_lower or "pullback" in st_lower:
        route = "B"
    elif "mean" in st_lower or st_lower in ("a", "mean_reversion"):
        route = "A"
    else:
        route = "A"

    pid = str(pos.get("product_id") or record.get("product_id") or "")
    fees = float(record.get("fees_usd") or record.get("fees") or 0.0)
    gross = float(record.get("gross_pnl_usd") or 0.0)
    net = float(record.get("net_pnl_usd") or 0.0)

    rm = record.get("realized_move_bps")
    entry_slip = None
    if rm is not None:
        try:
            entry_slip = max(0.0, abs(float(rm)) * 0.25)
        except (TypeError, ValueError):
            entry_slip = 0.0

    snap: Optional[str] = None
    ms = pos.get("market_snapshot") or record.get("market_snapshot")
    if ms is not None:
        try:
            snap = ms if isinstance(ms, str) else json.dumps(ms, default=str)
        except Exception:
            snap = None

    raw: Dict[str, Any] = {
        "trade_id": pos_id,
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "asset": pid or str(record.get("asset") or "BTC-USD"),
        "strategy_id": strat,
        "route_chosen": route,
        "route_a_score": float(record.get("router_score_a") or 0.5),
        "route_b_score": float(record.get("router_score_b") or 0.5),
        "regime": str(pos.get("entry_regime") or record.get("regime") or "unknown"),
        "timestamp_open": _iso_from_unix(opened) if opened > 0 else _iso_from_unix(0),
        "timestamp_close": _iso_from_unix(closed if closed > opened else opened + 1.0),
        "expected_edge_bps": float(record.get("expected_edge_bps") or 0.0),
        "net_pnl": net,
        "gross_pnl": gross,
        "fees_paid": fees,
        "exit_reason": str(exit_reason),
        "maker_taker": "taker" if str(record.get("execution_type") or "").find("market") >= 0 else "maker",
    }
    if entry_slip is not None:
        raw["entry_slippage_bps"] = float(entry_slip)
        raw["exit_slippage_bps"] = float(entry_slip)

    if str(exit_reason) == "stop_loss" or record.get("mistake_classification") == "hit_stop":
        raw["anomaly_flags"] = ["hard_stop_exit"]

    eid = pos.get("edge_id")
    if eid:
        raw["edge_id"] = str(eid)
    lane = pos.get("edge_lane")
    if lane:
        raw["edge_lane"] = str(lane)
    if snap:
        raw["market_snapshot_json"] = snap

    return raw
