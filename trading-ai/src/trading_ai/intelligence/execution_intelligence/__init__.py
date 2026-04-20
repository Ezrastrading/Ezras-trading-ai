"""Execution Intelligence Engine (EIE) — goal tracking and advisory daily plans (no trade forcing)."""

from __future__ import annotations

from trading_ai.intelligence.execution_intelligence.daily_plan import generate_daily_plan
from trading_ai.intelligence.execution_intelligence.evaluation import (
    attach_raw_trades,
    evaluate_goal_progress,
    infer_operating_mode,
)
from trading_ai.intelligence.execution_intelligence.goals import (
    GOAL_A,
    GOAL_B,
    GOAL_C,
    GOAL_D,
    GOAL_REGISTRY,
    get_goal,
)
from trading_ai.intelligence.execution_intelligence.persistence import (
    refresh_execution_intelligence,
    select_active_goal,
)
from trading_ai.intelligence.execution_intelligence.system_state import get_system_state

__all__ = [
    "GOAL_A",
    "GOAL_B",
    "GOAL_C",
    "GOAL_D",
    "GOAL_REGISTRY",
    "get_goal",
    "get_system_state",
    "evaluate_goal_progress",
    "generate_daily_plan",
    "infer_operating_mode",
    "attach_raw_trades",
    "refresh_execution_intelligence",
    "select_active_goal",
]
