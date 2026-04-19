"""Strategy research logging — non-authoritative; use guarded readers for consumption."""

from trading_ai.strategy_research.research_execution_guard import (
    RESEARCH_EXECUTION_BAN_MSG,
    assert_strategy_research_read_allowed,
)
from trading_ai.strategy_research.strategy_research_engine import (
    daily_summary_path,
    iter_research_log_entries,
    load_daily_summary_for_review,
    research_log_path,
    run_strategy_research_cycle,
)

__all__ = [
    "RESEARCH_EXECUTION_BAN_MSG",
    "assert_strategy_research_read_allowed",
    "daily_summary_path",
    "iter_research_log_entries",
    "load_daily_summary_for_review",
    "research_log_path",
    "run_strategy_research_cycle",
]
