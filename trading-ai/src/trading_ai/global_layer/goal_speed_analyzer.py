"""Project time-to-goal from capital truth + rolling net (not deposits as profit)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


def _rolling_daily_avg_net(trades: List[Dict[str, Any]], days: int = 14) -> float:
    cutoff = time.time() - days * 86400
    total = 0.0
    n = 0
    for t in trades:
        if not isinstance(t, dict):
            continue
        ts = t.get("logged_at") or t.get("exit_time")
        if ts is None:
            continue
        try:
            if isinstance(ts, (int, float)) and float(ts) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        total += float(t.get("net_pnl_usd") or 0.0)
        n += 1
    return total / max(days, 1)


def active_goal_label(
    equity: float,
    rolling_7d: float,
    *,
    goal_a_usd: float = 1000.0,
    goal_b_week: float = 1000.0,
    goal_c_week: float = 2000.0,
) -> str:
    if equity < goal_a_usd:
        return "A"
    if rolling_7d < goal_b_week:
        return "B"
    if rolling_7d < goal_c_week:
        return "C"
    return "POST_C"


def projected_days_to_goal_a(
    equity: float,
    trades: List[Dict[str, Any]],
    *,
    target: float = 1000.0,
) -> Tuple[float, float]:
    """Returns (projected_days, confidence 0-1)."""
    gap = max(0.0, target - equity)
    daily = _rolling_daily_avg_net(trades, 14)
    if daily <= 0:
        return float("inf"), 0.15
    days = gap / max(daily, 1e-6)
    conf = 0.75 if len(trades) >= 20 else 0.4
    return days, conf


def label_speed(projected_days: float) -> str:
    if projected_days == float("inf") or projected_days > 365:
        return "slow"
    if projected_days > 120:
        return "base"
    return "fast"


def identify_blockers(internal: Dict[str, Any]) -> List[Dict[str, Any]]:
    blockers: List[Dict[str, Any]] = []
    led = internal.get("capital_ledger") or {}
    dep = float(led.get("deposits_usd") or 0)
    realized = float(led.get("realized_pnl_usd") or 0)
    if dep > 0 and abs(realized) < dep * 0.05:
        blockers.append(
            {
                "name": "low_realized_vs_deposits",
                "scope": "capital",
                "severity": "medium",
                "explanation": "Deposits present; ensure progress metrics use earned PnL, not funding.",
            }
        )
    tc = int(internal.get("trade_count") or 0)
    if tc < 5:
        blockers.append(
            {
                "name": "thin_trade_sample",
                "scope": "global",
                "severity": "high",
                "explanation": "Not enough closed trades to estimate speed confidently.",
            }
        )
    return blockers
