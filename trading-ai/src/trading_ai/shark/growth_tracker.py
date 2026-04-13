"""Track portfolio growth vs monthly compound targets (starting from $25)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)

# (month_start_usd, month_target_usd) — ALL ARE MINIMUMS. Faster is always better.
MONTHLY_TARGETS: List[Tuple[float, float]] = [
    (25.0,          1_750.0),     # Month 1 — MINIMUM — faster is better
    (1_750.0,       8_000.0),     # Month 2 — MINIMUM — faster is better
    (8_000.0,      35_000.0),     # Month 3 — MINIMUM — faster is better
    (35_000.0,    120_000.0),     # Month 4 — MINIMUM — faster is better
    (120_000.0,   480_000.0),     # Month 5 — MINIMUM — faster is better
    (480_000.0,   780_000.0),     # Month 6 — MINIMUM — faster is better
    (780_000.0, 1_200_000.0),     # Year end — MINIMUM — faster is better
]

STRETCH_MULTIPLIER = 3.0  # Stretch targets = minimum × 3.0


def current_month_index(capital: float) -> int:
    """Return 1-based month index based on capital level (1 = Month 1, etc.)."""
    for i, (_, target) in enumerate(MONTHLY_TARGETS):
        if capital < target:
            return i + 1
    return len(MONTHLY_TARGETS)


def get_growth_status(
    current_capital: float,
    month_start_capital: Optional[float] = None,
    days_elapsed: Optional[int] = None,
) -> Dict:
    """
    Return growth status vs monthly minimum and stretch targets.

    Args:
        current_capital: Current USD capital.
        month_start_capital: Capital at start of current month.
                             Defaults to MONTHLY_TARGETS[month_idx-1][0].
        days_elapsed: Days elapsed this month. Defaults to current day-of-month.

    Returns dict with keys:
        current_capital, month_index, month_start_capital,
        monthly_target (=minimum_target), minimum_target, stretch_target,
        days_elapsed, progress_pct, projected_month_end,
        on_pace, trajectory, which_target_on_pace_for.
    """
    month_idx = current_month_index(current_capital)
    table_idx = min(month_idx - 1, len(MONTHLY_TARGETS) - 1)
    default_start, minimum_target = MONTHLY_TARGETS[table_idx]
    stretch_target = round(minimum_target * STRETCH_MULTIPLIER, 2)

    month_start = month_start_capital if (month_start_capital is not None and month_start_capital > 0) else default_start

    if days_elapsed is None:
        days_elapsed = datetime.now(timezone.utc).day
    days_elapsed = max(days_elapsed, 0)
    days_in_month = 30  # approximate

    needed = minimum_target - month_start
    achieved = current_capital - month_start
    if needed > 0:
        progress_pct = round((achieved / needed) * 100, 1)
    else:
        progress_pct = 100.0

    # Linear projection from daily run-rate
    if days_elapsed > 0:
        daily_gain = achieved / days_elapsed
        projected_month_end = round(month_start + daily_gain * days_in_month, 2)
    else:
        projected_month_end = round(current_capital, 2)

    # Compare progress% to expected% at this point in the month
    expected_pct = (days_elapsed / days_in_month) * 100 if days_in_month > 0 else 100.0
    if progress_pct >= expected_pct * 1.1:
        trajectory = "ahead"
    elif progress_pct >= expected_pct * 0.85:
        trajectory = "on_pace"
    elif progress_pct >= expected_pct * 0.5:
        trajectory = "behind"
    else:
        trajectory = "critical"

    # Which target is the projection on pace for?
    if projected_month_end >= stretch_target:
        which_target_on_pace_for = "stretch"
    elif projected_month_end >= minimum_target:
        which_target_on_pace_for = "minimum"
    else:
        which_target_on_pace_for = "below_minimum"

    return {
        "current_capital": current_capital,
        "month_index": month_idx,
        "month_start_capital": month_start,
        "monthly_target": minimum_target,   # alias for backward compat
        "minimum_target": minimum_target,
        "stretch_target": stretch_target,
        "days_elapsed": days_elapsed,
        "progress_pct": progress_pct,
        "projected_month_end": projected_month_end,
        "on_pace": trajectory in ("ahead", "on_pace"),
        "trajectory": trajectory,
        "which_target_on_pace_for": which_target_on_pace_for,
    }


def format_growth_memo(status: Dict) -> str:
    """Format growth status as a Telegram-ready memo block."""
    return (
        "📈 GROWTH TRACKER\n"
        f"Capital: ${status['current_capital']:,.2f}\n"
        f"Month {status['month_index']} — MIN: ${status['minimum_target']:,.0f}"
        f" | STRETCH: ${status['stretch_target']:,.0f}\n"
        f"Progress: {status['progress_pct']:.1f}% | Trajectory: {status['trajectory']}\n"
        f"On pace for: {status['which_target_on_pace_for'].replace('_', ' ')}\n"
        f"Projected month end: ${status['projected_month_end']:,.2f}"
    )


def check_trajectory(current_capital: Optional[float] = None) -> Optional[str]:
    """
    Called by the scheduler every 30 min.
    Returns trajectory string ('ahead'/'on_pace'/'behind'/'critical') or None on error.
    """
    try:
        if current_capital is None:
            from trading_ai.shark.state_store import load_capital
            current_capital = load_capital().current_capital
        status = get_growth_status(current_capital)
        logger.debug("growth trajectory=%s capital=$%.2f", status["trajectory"], current_capital)
        return status["trajectory"]
    except Exception as exc:
        logger.warning("growth check error: %s", exc)
        return None
