"""Trades/day, PnL windows, capital efficiency — projections only from observed stats."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        raw = str(s).replace("Z", "+00:00")
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def compute_performance_snapshot(trades: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Aggregates from actual closes only — weekly projection is ``mean_daily_pnl * 7`` when
    at least one day of data exists; otherwise null (no fabrication).
    """
    if not trades:
        return {
            "trades_per_day": None,
            "pnl_per_day": None,
            "pnl_per_week_observed": None,
            "expectancy": None,
            "capital_efficiency": None,
            "weekly_projection_linear": None,
            "required_improvement_notes": "insufficient_sample",
        }

    by_day: Dict[str, List[float]] = {}
    for t in trades:
        ts = _parse_ts(t.get("timestamp_close"))
        if ts is None:
            continue
        day = ts.date().isoformat()
        by_day.setdefault(day, []).append(float(t.get("net_pnl") or 0.0))

    if not by_day:
        return {
            "trades_per_day": None,
            "pnl_per_day": None,
            "pnl_per_week_observed": None,
            "expectancy": None,
            "capital_efficiency": None,
            "weekly_projection_linear": None,
            "required_improvement_notes": "no_parseable_timestamps",
        }

    daily_pnls = [sum(v) for v in by_day.values()]
    daily_counts = [len(v) for v in by_day.values()]
    mean_pnl_day = sum(daily_pnls) / len(daily_pnls)
    mean_trades = sum(daily_counts) / len(daily_counts)
    week_pnl = sum(daily_pnls[-7:]) if len(daily_pnls) >= 1 else None

    total_net = sum(float(t.get("net_pnl") or 0.0) for t in trades)
    n = len(trades)
    expectancy = total_net / n if n else None

    projection = None
    if len(by_day) >= 3:
        projection = mean_pnl_day * 7.0

    gap = None
    if expectancy is not None and expectancy < 0:
        gap = "expectancy_negative_need_edge_or_risk_fix"
    elif expectancy == 0:
        gap = "expectancy_zero_need_positive_edge"

    return {
        "trades_per_day": mean_trades,
        "pnl_per_day": mean_pnl_day,
        "pnl_per_week_observed": week_pnl,
        "expectancy": expectancy,
        "capital_efficiency": (total_net / n) if n else None,
        "weekly_projection_linear": projection,
        "required_improvement_notes": gap or "none",
    }
