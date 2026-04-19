"""Weekly aggregates from observed trades — projections are labeled extrapolations, not fabricated."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        raw = str(s).replace("Z", "+00:00")
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def events_in_window(
    events: Sequence[Mapping[str, Any]],
    *,
    days: float = 7.0,
    reference: Optional[datetime] = None,
) -> List[Mapping[str, Any]]:
    ref = reference or datetime.now(timezone.utc)
    cutoff = ref.timestamp() - days * 86400.0
    out: List[Mapping[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        tc = _parse_ts(ev.get("timestamp_close"))
        if tc is None:
            continue
        if tc.timestamp() >= cutoff:
            out.append(ev)
    return out


def weekly_performance_summary(
    events: Sequence[Mapping[str, Any]],
    *,
    days: float = 7.0,
) -> Dict[str, Any]:
    """
    Observed window stats. ``projected_weekly_pnl`` is **only** ``observed_daily_mean_net_pnl * 7``
    when at least one closed trade exists in-window; otherwise null with explicit reason.
    """
    win = events_in_window(events, days=days)
    pnls: List[float] = []
    for ev in win:
        try:
            pnls.append(float(ev.get("net_pnl") or 0.0))
        except (TypeError, ValueError):
            continue
    n = len(pnls)
    total = sum(pnls)
    mean = total / n if n else 0.0
    daily_mean = mean / max(days / 7.0, 1e-9) if n else 0.0

    projected = None
    projection_note = None
    if n > 0:
        projected = daily_mean * 7.0
        projection_note = (
            "linear_scaling_from_observed_window: "
            f"sum_net_pnl={total:.6f} over ~{days}d, n={n}; "
            "not a forecast of future performance"
        )
    else:
        projection_note = "no_trades_in_window_projected_null"

    fees = 0.0
    for ev in win:
        try:
            fees += float(ev.get("fees_paid") or 0.0)
        except (TypeError, ValueError):
            pass

    wins = len([p for p in pnls if p > 0])
    win_rate = wins / n if n else 0.0

    return {
        "window_days": days,
        "total_trades": n,
        "total_pnl": total,
        "pnl_per_trade": mean,
        "win_rate": win_rate,
        "total_fees_paid": fees,
        "expectancy_net_per_trade": mean,
        "capital_efficiency_note": "not_computed_without_account_equity_series",
        "projected_weekly_pnl": projected,
        "projection_method": "observed_daily_mean_times_7_within_window" if n else None,
        "projection_disclaimer": projection_note,
    }


def required_improvement_to_target(
    observed_mean_daily_net: float,
    *,
    target_weekly_pnl: float,
    days_in_window: float = 7.0,
) -> Dict[str, Any]:
    """
    Gap between extrapolated weekly rate and a **stated** target (caller-supplied).
    Does not invent targets.
    """
    current_weekly = observed_mean_daily_net * 7.0
    gap = target_weekly_pnl - current_weekly
    return {
        "target_weekly_pnl": target_weekly_pnl,
        "extrapolated_from_observed_daily": current_weekly,
        "gap_to_target": gap,
        "days_in_window_assumption": days_in_window,
    }
