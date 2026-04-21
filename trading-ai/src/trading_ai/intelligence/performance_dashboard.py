"""
Human-readable performance snapshot from closed journal trades.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

OUTPUT_NAME = "performance_snapshot.json"


def output_path() -> Path:
    return shark_state_path(OUTPUT_NAME)


def _profit_factor(wins: List[float], losses: List[float]) -> float:
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    if gl <= 1e-12:
        return float("inf") if gw > 0 else 0.0
    return gw / gl


def compute_snapshot(closed_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = [float(t.get("pnl_usd", 0) or 0) for t in closed_trades if str(t.get("outcome")).lower() == "win"]
    losses = [float(t.get("pnl_usd", 0) or 0) for t in closed_trades if str(t.get("outcome")).lower() == "loss"]
    n = len(closed_trades)
    wr = (len(wins) / n) if n else 0.0
    total_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in closed_trades)
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    pf = _profit_factor(wins, losses)

    snap = {
        "win_rate": round(wr, 6),
        "total_pnl": round(total_pnl, 4),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "profit_factor": float(pf) if pf != float("inf") else None,
    }
    if pf == float("inf"):
        snap["profit_factor_inf"] = True
    return snap


def refresh_performance_dashboard(
    *,
    trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Recompute metrics and write ``performance_snapshot.json``."""
    if trades is None:
        from trading_ai.shark.trade_journal import get_all_trades

        all_t = get_all_trades()
    else:
        all_t = trades
    closed = [
        t
        for t in all_t
        if isinstance(t, dict) and str(t.get("outcome", "pending")).lower() not in ("pending", "")
    ]
    snap = compute_snapshot(closed)
    p = output_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    logger.info("performance_dashboard: wrote %s %s", p, snap)
    return snap
