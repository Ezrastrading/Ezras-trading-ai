"""Structured financial goals for the Execution Intelligence Engine (advisory only)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Goal identifiers
GOAL_A = "GOAL_A"
GOAL_B = "GOAL_B"
GOAL_C = "GOAL_C"
GOAL_D = "GOAL_D"


def _goal_a() -> Dict[str, Any]:
    return {
        "id": GOAL_A,
        "name": "First $1K",
        "target_profit": 1000.0,
        "phase": "bootstrapping",
        "guidance": "Establish consistent edge and capital growth",
        "objective_metrics": [
            "cumulative realized net profit (USD) toward $1,000",
            "trade count and fee-adjusted expectancy over rolling windows",
        ],
        "success_conditions": [
            "ledger realized net PnL (or federated trade sum aligned with ledger) reaches >= $1,000 profit since goal baseline",
            "no unresolved hard-stop or governance halt blocking progression reporting",
        ],
        "failure_conditions": [
            "sustained negative expectancy while increasing size",
            "capital erosion from fees without edge recovery (diagnostic, not a halt trigger)",
        ],
        "progression_signals": [
            "rising rolling 7d net after fees",
            "stable or improving win rate with controlled drawdown",
            "strategy_scores not collapsing across primary setups",
        ],
    }


def _goal_b() -> Dict[str, Any]:
    return {
        "id": GOAL_B,
        "name": "$1K/week consistency",
        "target_weekly_profit": 1000.0,
        "required_weeks": 2,
        "objective_metrics": [
            "global calendar-week net profit (UTC) from closed trades",
            "two consecutive weeks each >= $1,000 net",
        ],
        "success_conditions": [
            "last two ISO weeks (UTC) each have sum(net_pnl_usd) >= $1,000",
        ],
        "failure_conditions": [
            "two consecutive weeks below $500 net while targeting $1K/week pace",
        ],
        "progression_signals": [
            "week-over-week net trending toward $1K",
            "loss clusters shrinking vs prior month",
        ],
    }


def _goal_c() -> Dict[str, Any]:
    return {
        "id": GOAL_C,
        "name": "$2K/week per avenue",
        "target_weekly_profit": 2000.0,
        "objective_metrics": [
            "per-avenue ISO week net profit from trade_memory rows",
            "minimum across active avenues vs $2,000/week target",
        ],
        "success_conditions": [
            "each active avenue with >= min weekly trades has last UTC week net >= $2,000",
        ],
        "failure_conditions": [
            "one avenue carries the stack while others are flat (concentration warning)",
        ],
        "progression_signals": [
            "multiple avenues posting positive weekly nets",
            "per-avenue score stability in strategy_scores.json",
        ],
    }


def _goal_d() -> Dict[str, Any]:
    return {
        "id": GOAL_D,
        "name": "$3K/week per avenue",
        "target_weekly_profit": 3000.0,
        "objective_metrics": [
            "per-avenue ISO week net profit vs $3,000/week",
        ],
        "success_conditions": [
            "each qualifying avenue last UTC week net >= $3,000",
        ],
        "failure_conditions": [
            "elevated volatility + thinning liquidity with rising size",
        ],
        "progression_signals": [
            "sustained $2K/week per avenue before sizing to $3K pace",
            "execution quality metrics stable (spreads, slippage fields populated)",
        ],
    }


GOAL_REGISTRY: Dict[str, Dict[str, Any]] = {
    GOAL_A: _goal_a(),
    GOAL_B: _goal_b(),
    GOAL_C: _goal_c(),
    GOAL_D: _goal_d(),
}


def get_goal(goal_id: str) -> Optional[Dict[str, Any]]:
    g = GOAL_REGISTRY.get(goal_id)
    return dict(g) if g else None


def default_goal_order() -> List[str]:
    return [GOAL_A, GOAL_B, GOAL_C, GOAL_D]
