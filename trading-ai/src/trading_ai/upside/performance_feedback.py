"""Persisted feedback loops for latency, venues, and edges (measurement — no gate bypass)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


def latency_success_rate_path() -> Path:
    return ezras_runtime_root() / "latency_success_rate.json"


def venue_scores_path() -> Path:
    return ezras_runtime_root() / "venue_scores.json"


def edge_feedback_log_path() -> Path:
    return ezras_runtime_root() / "edge_feedback.jsonl"


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else dict(default)
    except (OSError, json.JSONDecodeError, TypeError):
        return dict(default)


def record_latency_outcome(*, success: bool, venue: str, product_id: str = "") -> None:
    p = latency_success_rate_path()
    st = _read_json(p, {"attempts": 0, "successes": 0, "by_venue": {}})
    st["attempts"] = int(st.get("attempts") or 0) + 1
    if success:
        st["successes"] = int(st.get("successes") or 0) + 1
    bv = st.get("by_venue") or {}
    if not isinstance(bv, dict):
        bv = {}
    key = (venue or "unknown").strip().lower()
    cur = bv.get(key) or {"attempts": 0, "successes": 0}
    cur["attempts"] = int(cur.get("attempts") or 0) + 1
    if success:
        cur["successes"] = int(cur.get("successes") or 0) + 1
    bv[key] = cur
    st["by_venue"] = bv
    st["updated_unix"] = time.time()
    st["last_product_id"] = product_id
    att = max(1, int(st["attempts"]))
    st["success_rate"] = float(st["successes"]) / float(att)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=2), encoding="utf-8")


def record_venue_trade_feedback(
    venue: str,
    *,
    pnl_delta: float,
    win: bool,
    execution_quality_hint: float = 0.0,
    window: int = 50,
) -> None:
    """Rolling aggregates for :mod:`trading_ai.capital.router` (lightweight EMA)."""
    p = venue_scores_path()
    st = _read_json(p, {"venues": {}, "updated_unix": 0.0})
    venues = st.get("venues") or {}
    if not isinstance(venues, dict):
        venues = {}
    key = (venue or "unknown").strip().lower()
    row = venues.get(key) or {}
    pnl_hist = list(row.get("pnl_recent") or [])
    pnl_hist.append(float(pnl_delta))
    pnl_hist = pnl_hist[-window:]
    trades = int(row.get("trades") or 0) + 1
    wins = int(row.get("wins") or 0) + (1 if win else 0)
    last_50 = sum(pnl_hist)
    win_rate = wins / max(1, min(trades, window))
    dd = float(row.get("drawdown") or 0.0)
    peak = float(row.get("peak_pnl_cum") or 0.0)
    cum = float(row.get("cum_pnl") or 0.0) + float(pnl_delta)
    peak = max(peak, cum)
    dd = max(dd, peak - cum)
    ex = float(row.get("execution_quality_score") or 0.5)
    if execution_quality_hint > 0:
        ex = 0.85 * ex + 0.15 * max(0.0, min(1.0, execution_quality_hint))
    venues[key] = {
        "last_50_trades_pnl": last_50,
        "win_rate": win_rate,
        "drawdown": min(1.0, dd / max(1.0, abs(last_50) + 1.0)),
        "execution_quality_score": ex,
        "pnl_recent": pnl_hist,
        "trades": trades,
        "wins": wins,
        "cum_pnl": cum,
        "peak_pnl_cum": peak,
        "shutdown_flag": bool(row.get("shutdown_flag")),
    }
    st["venues"] = venues
    st["updated_unix"] = time.time()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=2), encoding="utf-8")


def append_edge_feedback(record: Mapping[str, Any]) -> None:
    path = edge_feedback_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(record), default=str) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def record_closed_trade_feedback(merged: Mapping[str, Any]) -> None:
    """Called after a closed trade is validated — updates JSON / JSONL stores."""
    venue = str(merged.get("avenue_name") or merged.get("avenue_id") or "unknown")
    try:
        pnl = float(merged.get("net_pnl") or merged.get("net_pnl_usd") or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
    win = pnl > 0
    lat_ok = bool(merged.get("latency_trade"))
    if lat_ok or merged.get("latency_signal_types"):
        record_latency_outcome(success=win, venue=venue, product_id=str(merged.get("asset") or ""))
    record_venue_trade_feedback(venue, pnl_delta=pnl, win=win)
    append_edge_feedback(
        {
            "ts": time.time(),
            "edge_id": merged.get("edge_id"),
            "net_pnl": pnl,
            "venue": venue,
            "latency_trade": merged.get("latency_trade"),
        }
    )
