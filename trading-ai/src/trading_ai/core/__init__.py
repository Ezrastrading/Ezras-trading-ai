"""Core trading primitives — position truth, capital, strategy validation."""

from trading_ai.core.capital_engine import CapitalEngine, CapitalLimits, capital_preflight_block
from trading_ai.core.portfolio_engine import PortfolioEngine, PortfolioState, maybe_rebalance_if_due
from trading_ai.core.position_engine import (
    Fill,
    PositionState,
    compute_total_pnl,
    compute_unrealized_pnl,
    update_position_from_fill,
)
from trading_ai.core.system_guard import SystemGuard, clear_trading_halt, get_system_guard

__all__ = [
    "CapitalEngine",
    "CapitalLimits",
    "PortfolioEngine",
    "PortfolioState",
    "maybe_rebalance_if_due",
    "Fill",
    "PositionState",
    "capital_preflight_block",
    "compute_total_pnl",
    "compute_unrealized_pnl",
    "update_position_from_fill",
    "SystemGuard",
    "clear_trading_halt",
    "get_system_guard",
]
