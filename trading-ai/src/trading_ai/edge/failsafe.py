"""Rolling health checks — reduce or halt when metrics degrade."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from trading_ai.edge.scoring import compute_edge_metrics


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def rolling_expectancy_failsafe(
    events: Sequence[Mapping[str, Any]],
    *,
    edge_id: str,
    window: int = 15,
) -> Dict[str, Any]:
    """Last ``window`` trades for edge — halt if post_fee_expectancy < 0."""
    rows = [e for e in events if isinstance(e, dict) and str(e.get("edge_id") or "") == edge_id]
    tail = rows[-window:] if len(rows) > window else rows
    m = compute_edge_metrics(tail, edge_id)
    halt = m.total_trades >= _env_int("EDGE_FAILSAFE_MIN_TRADES", 8) and m.post_fee_expectancy < 0
    return {
        "edge_id": edge_id,
        "halt_trading": halt,
        "rolling_trades": m.total_trades,
        "rolling_post_fee_expectancy": m.post_fee_expectancy,
        "reason": "rolling_expectancy_negative" if halt else "ok",
    }


def global_anomaly_failsafe(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Flag abnormal loss streak or pipeline anomalies from event flags."""
    reasons: List[str] = []
    recent = [e for e in events if isinstance(e, dict)][-50:]
    net_sum = 0.0
    for e in recent:
        try:
            net_sum += float(e.get("net_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
    if len(recent) >= 10 and net_sum < -abs(_env_float("EDGE_GLOBAL_LOSS_USD_HALT", 500.0)):
        reasons.append("abnormal_cumulative_loss_window")

    for e in recent[-10:]:
        flags = e.get("anomaly_flags") or []
        if isinstance(flags, list) and "data_pipeline_failure" in [str(x) for x in flags]:
            reasons.append("data_pipeline_failure_flag")
        if str(e.get("health_state") or "") == "error":
            reasons.append("health_state_error")

    return {
        "halt_trading": bool(reasons),
        "reasons": reasons,
    }


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def combined_failsafe(
    events: Sequence[Mapping[str, Any]],
    *,
    edge_id: Optional[str] = None,
) -> Dict[str, Any]:
    g = global_anomaly_failsafe(events)
    if not edge_id:
        return {"halt_trading": g["halt_trading"], "global": g, "edge": None}
    r = rolling_expectancy_failsafe(events, edge_id=edge_id)
    halt = g["halt_trading"] or r["halt_trading"]
    return {"halt_trading": halt, "global": g, "edge": r}
