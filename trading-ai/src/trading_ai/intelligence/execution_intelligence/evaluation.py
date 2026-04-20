"""Goal progress evaluation and operating mode (advisory — does not change risk gates)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.execution_intelligence.goals import GOAL_A, GOAL_B, GOAL_C, GOAL_D
from trading_ai.intelligence.execution_intelligence.system_state import (
    global_weekly_totals_by_iso_week,
    weekly_net_by_avenue,
)
from trading_ai.intelligence.execution_intelligence.time_utils import last_n_iso_week_ids


def _num(s: Any) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def infer_operating_mode(system_state: Dict[str, Any]) -> str:
    """
    Advisory label only — does not modify execution or risk config.

    Modes: aggressive_growth | controlled_growth | stabilization | capital_protection
    """
    wr = system_state.get("win_rate")
    n = int((system_state.get("data_quality") or {}).get("trade_rows") or 0)
    dd = _num(system_state.get("max_drawdown"))
    wk = _num(system_state.get("weekly_pnl"))
    ess = system_state.get("edge_stability_score")
    roll7 = _num((system_state.get("ledger_snapshot") or {}).get("rolling_7d_net_profit"))

    # Capital protection: sustained poor outcomes with enough sample
    if n >= 15 and wr is not None and wr < 0.35 and dd > 150:
        return "capital_protection"
    if n >= 10 and roll7 < -200 and wk < 0:
        return "capital_protection"

    # Stabilization: weak or unknown edge quality, negative week, or volatile regime
    vol = (system_state.get("volatility_state") or {}).get("label")
    if ess is not None and ess < 0.35:
        return "stabilization"
    if wr is not None and n >= 8 and wr < 0.42:
        return "stabilization"
    if wk < 0 and n >= 5:
        return "stabilization"
    if vol == "elevated" and (wk < 500 or wk < roll7):
        return "stabilization"

    # Aggressive growth only when clearly healthy (still advisory)
    if (
        n >= 12
        and wr is not None
        and wr >= 0.52
        and wk >= 400
        and roll7 >= 300
        and (ess is None or ess >= 0.55)
    ):
        return "aggressive_growth"

    return "controlled_growth"


def evaluate_goal_progress(goal: Dict[str, Any], system_state: Dict[str, Any]) -> Dict[str, Any]:
    gid = str(goal.get("id") or "")
    led = system_state.get("ledger_snapshot") or {}
    realized = _num(led.get("realized_pnl_net"))
    now_ts = datetime.now(timezone.utc).timestamp()

    # Rebuild week buckets from recent_trade_outcomes is insufficient — need full history.
    # Caller passes enriched state via goal evaluation entrypoint with `trades` optional.
    trades: List[Dict[str, Any]] = list(system_state.get("_raw_trades") or [])

    blockers: List[str] = []
    strengths: List[str] = []
    progress_pct = 0.0
    current_position = ""
    trajectory_status = "behind"
    estimated_days_remaining: Optional[float] = None

    n_trades = int((system_state.get("data_quality") or {}).get("trade_rows") or 0)
    tc_week = int(system_state.get("trade_count_week") or 0)

    if gid == GOAL_A:
        target = _num(goal.get("target_profit")) or 1000.0
        progress_pct = max(0.0, min(100.0, 100.0 * realized / target)) if target > 0 else 0.0
        current_position = f"Realized net ≈ ${realized:.2f} toward ${target:.0f} (ledger)"
        if realized >= target:
            trajectory_status = "ahead"
        elif progress_pct >= 45:
            trajectory_status = "on_track"
        else:
            trajectory_status = "behind"
        if n_trades < 5:
            blockers.append("insufficient trade history for confident pace estimate")
        if tc_week < 3:
            blockers.append("low trade frequency")
        if realized > 0 and progress_pct > 20:
            strengths.append("positive realized PnL toward first target")
        if progress_pct < 100 and realized > 0:
            remaining = target - realized
            daily = _num(system_state.get("daily_pnl"))
            if daily > 0:
                estimated_days_remaining = max(1.0, remaining / max(daily, 1e-6))
            else:
                wk = _num(system_state.get("weekly_pnl"))
                if wk > 0:
                    estimated_days_remaining = max(1.0, 7.0 * remaining / max(wk, 1e-6))

    elif gid == GOAL_B:
        target = _num(goal.get("target_weekly_profit")) or 1000.0
        req_weeks = int(goal.get("required_weeks") or 2)
        buckets = global_weekly_totals_by_iso_week(trades, now_ts=now_ts)
        last_ids = last_n_iso_week_ids(now_ts, max(4, req_weeks + 2))
        last_two = last_ids[:2]
        nets = [buckets.get(wid, 0.0) for wid in last_two]
        met = sum(1 for x in nets if x >= target)
        # Progress: average of last two weeks vs target, scaled
        avg_two = (nets[0] + nets[1]) / 2.0 if len(nets) >= 2 else (nets[0] if nets else 0.0)
        progress_pct = max(0.0, min(100.0, 50.0 * (avg_two / target))) if target > 0 else 0.0
        if met >= req_weeks:
            progress_pct = max(progress_pct, 100.0)
        current_position = f"Last two ISO weeks net (UTC): {nets[0]:.2f}, {nets[1]:.2f} vs ${target:.0f}/wk target"
        if len(last_two) >= 2 and nets[0] >= target and nets[1] >= target:
            trajectory_status = "ahead"
        elif len(last_two) >= 2 and nets[0] >= target * 0.85 and nets[1] >= target * 0.85:
            trajectory_status = "on_track"
        else:
            trajectory_status = "behind"
        if tc_week < 8:
            blockers.append("low trade frequency")
        if avg_two < target * 0.5:
            blockers.append("weekly net below consistency target")
        if len(buckets) >= 2:
            strengths.append("multi-week history available for pacing")

    elif gid in (GOAL_C, GOAL_D):
        target = _num(goal.get("target_weekly_profit")) or (2000.0 if gid == GOAL_C else 3000.0)
        day_start = datetime.fromtimestamp(now_ts, tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_start = day_start - timedelta(days=day_start.weekday())
        t_week = week_start.timestamp()
        by_av = weekly_net_by_avenue(trades, week_start_ts=t_week, now_ts=now_ts)
        avenues = system_state.get("current_active_avenues") or []
        if not by_av:
            progress_pct = 0.0
            current_position = "No per-avenue weekly PnL (missing timestamps or avenues)"
            blockers.append("insufficient capital deployment or unattributed avenue rows")
        else:
            vals = [by_av.get(av, 0.0) for av in avenues] if avenues else list(by_av.values())
            floor = min(vals) if vals else 0.0
            progress_pct = max(0.0, min(100.0, 100.0 * floor / target)) if target > 0 else 0.0
            current_position = f"Weakest avenue this UTC week: ${floor:.2f} vs ${target:.0f}/wk target"
            if floor >= target:
                trajectory_status = "ahead"
            elif floor >= target * 0.75:
                trajectory_status = "on_track"
            else:
                trajectory_status = "behind"
            if len([v for v in vals if v > 0]) < 2 and len(by_av) > 1:
                blockers.append("concentration — not all avenues contributing")
            if floor < target * 0.4:
                blockers.append("weak edge stability vs weekly target (per avenue)")
        if tc_week < 10:
            blockers.append("low trade frequency")

    wr = system_state.get("win_rate")
    ess = system_state.get("edge_stability_score")
    if ess is not None and ess < 0.4:
        blockers.append("weak edge stability (strategy score dispersion)")
    if wr is not None and n_trades >= 10 and wr < 0.4:
        blockers.append("overtrading losses" if tc_week > 25 else "win rate below breakeven pressure")

    # Deduplicate blockers
    blockers = list(dict.fromkeys([b for b in blockers if b]))

    return {
        "progress_pct": round(progress_pct, 4),
        "current_position": current_position,
        "trajectory_status": trajectory_status,
        "blockers": blockers,
        "strengths": [s for s in strengths if s],
        "estimated_days_remaining": None if estimated_days_remaining is None else round(estimated_days_remaining, 2),
        "goal_id": gid,
    }


def attach_raw_trades(system_state: Dict[str, Any], trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mutable helper for evaluation — internal key ``_raw_trades``."""
    s = dict(system_state)
    s["_raw_trades"] = trades
    return s
