"""Daily / next-day plan — advisory constraints aligned with governance (never forces trades)."""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.intelligence.execution_intelligence.evaluation import infer_operating_mode


def generate_daily_plan(goal: Dict[str, Any], system_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce focus lists and constraints. Does not override stops, sizing, or venue risk config.
    """
    mode = infer_operating_mode(system_state)
    gid = str(goal.get("id") or "")
    tc_day = int(system_state.get("trade_count_today") or 0)
    tc_week = int(system_state.get("trade_count_week") or 0)
    wr = system_state.get("win_rate")
    ess = system_state.get("edge_stability_score")
    vol = (system_state.get("volatility_state") or {}).get("label") or "unknown"
    gates = system_state.get("active_gates") or []

    today_focus: List[str] = []
    tomorrow_focus: List[str] = []
    execution_constraints: List[str] = [
        "Respect max risk per trade from governance / position sizing policy",
        "Do not override stop losses or hard stops",
        "No revenge trading — pause after repeated losses per existing discipline hooks",
        "Spread threshold limits remain authoritative (abstain on wide spreads)",
    ]
    priority_actions: List[str] = []
    avoid_actions: List[str] = [
        "Chasing pumps",
        "Thin liquidity trades without explicit size reduction",
        "Increasing risk beyond configured caps to 'catch up' on goals",
    ]

    if mode == "capital_protection":
        today_focus.append("Preserve capital — reduce exploratory lanes until metrics stabilize")
        tomorrow_focus.append("Re-assess expectancy on a clean sample of trades after stabilization")
        priority_actions.append("Stand down Gate B unless spreads are tight and confidence is high")
        priority_actions.append("Favor reconciliation and inventory truth over new entries")

    elif mode == "stabilization":
        today_focus.append("Tighten setup quality — fewer, higher-conviction trades")
        tomorrow_focus.append("Review last week’s loss clusters vs regime tags in trade_memory")
        priority_actions.append("Run Gate A on high-liquidity pairs only when signals align")
        priority_actions.append("Run Gate B only on tight spreads")

    elif mode == "aggressive_growth":
        today_focus.append("Increase constructive activity toward goal pace — still within risk caps")
        tomorrow_focus.append("Scale repetition of setups with validated post-fee expectancy")
        priority_actions.append("Run Gate A aggressively within existing risk and spread rules")
        priority_actions.append("Run Gate B only on tight spreads")

    else:  # controlled_growth
        today_focus.append("Maintain steady execution quality while progressing toward the active goal")
        tomorrow_focus.append("Carry forward what worked; cut what failed fee-adjusted expectancy")
        priority_actions.append("Balance Gate A and Gate B per current edge signals (no volume for its own sake)")

    # Goal-specific nudges (advisory)
    if gid == "GOAL_A":
        if tc_week < 15:
            today_focus.append("Increase trade count toward operational band (e.g. 60–80/week) only if edges validate")
        tomorrow_focus.append("Track cumulative realized net vs $1K — use ledger truth, not mental accounting")

    if gid == "GOAL_B":
        today_focus.append("Focus on sustainable weekly net — two consecutive strong ISO weeks")
        tomorrow_focus.append("Avoid low-confidence setups that add fee drag without expectancy")

    if gid in ("GOAL_C", "GOAL_D"):
        today_focus.append("Rotate attention across avenues — weakest avenue sets the floor")
        tomorrow_focus.append("Per-avenue weekly net must lift together; avoid single-avenue heroics")

    if vol == "elevated":
        today_focus.append("Volatility elevated — favor liquid products; shrink size per policy")
        avoid_actions.append("Oversized positions in fast markets without explicit slippage headroom")

    if ess is not None and ess < 0.45:
        today_focus.append("Edge stability weak — prioritize setup quality over frequency")

    if wr is not None and wr < 0.45 and tc_week > 20:
        today_focus.append("Win rate pressure — review exit reasons and fee load before adding frequency")

    if "A" in gates and "B" in gates:
        tomorrow_focus.append("Compare Gate A vs Gate B fee-adjusted results over the same window")

    # De-dup while preserving order
    def _uniq(xs: List[str]) -> List[str]:
        return list(dict.fromkeys([x for x in xs if x]))

    return {
        "today_focus": _uniq(today_focus),
        "tomorrow_focus": _uniq(tomorrow_focus),
        "execution_constraints": _uniq(execution_constraints),
        "priority_actions": _uniq(priority_actions),
        "avoid_actions": _uniq(avoid_actions),
        "mode": mode,
        "disclaimer": "Advisory decision-support only — does not modify risk engines or execute trades.",
    }
