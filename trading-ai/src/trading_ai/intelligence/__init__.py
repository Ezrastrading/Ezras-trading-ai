"""Real-world trading intelligence — gating and discipline (no strategy/edge logic)."""

from trading_ai.intelligence.abstain_engine import should_abstain
from trading_ai.intelligence.adaptive_sizing import compute_size_multiplier, load_multiplier_from_journal
from trading_ai.intelligence.capital_intelligence import best_and_worst_avenues, shift_capital
from trading_ai.intelligence.cooldown import cooldown_active
from trading_ai.intelligence.cooldown_engine import COOLDOWN_SECONDS, cooldown_active as cooldown_active_engine
from trading_ai.intelligence.edge_filter import passes_edge_filter
from trading_ai.intelligence.edge_threshold import MIN_EDGE_USD, passes_edge_threshold
from trading_ai.intelligence.fee_engine import estimate_total_fees, evaluate_fee_gate, is_trade_profitable
from trading_ai.intelligence.first_20 import adjust_for_first_20
from trading_ai.intelligence.first_20_protocol import apply_position_scale, mode as first_20_mode
from trading_ai.intelligence.market_filter import passes_market_conditions
from trading_ai.intelligence.market_reality import evaluate_market_conditions, orderbook_from_market_underlying
from trading_ai.intelligence.performance_dashboard import refresh_performance_dashboard
from trading_ai.intelligence.performance_guard import adjust_for_loss_streak
from trading_ai.intelligence.performance_snapshot import refresh_default_performance_snapshot, update_performance_snapshot
from trading_ai.intelligence.capital_router import adjust_capital_allocation
from trading_ai.intelligence.confidence_filter import passes_confidence
from trading_ai.intelligence.trade_gate import should_execute_trade

__all__ = [
    "passes_edge_filter",
    "passes_market_conditions",
    "cooldown_active",
    "adjust_for_loss_streak",
    "adjust_capital_allocation",
    "passes_confidence",
    "adjust_for_first_20",
    "update_performance_snapshot",
    "should_execute_trade",
    "refresh_default_performance_snapshot",
    "evaluate_market_conditions",
    "orderbook_from_market_underlying",
    "estimate_total_fees",
    "is_trade_profitable",
    "evaluate_fee_gate",
    "MIN_EDGE_USD",
    "passes_edge_threshold",
    "COOLDOWN_SECONDS",
    "cooldown_active_engine",
    "compute_size_multiplier",
    "load_multiplier_from_journal",
    "shift_capital",
    "best_and_worst_avenues",
    "should_abstain",
    "apply_position_scale",
    "first_20_mode",
    "refresh_performance_dashboard",
]
