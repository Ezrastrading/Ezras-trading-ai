"""
Canonical live trade **open** gate — enforced invariant for this repository.

================================================================================
INVARIANT (mandatory for any code path that submits or persists a **new** open)
================================================================================

All future live trade executors (Kalshi, other venues, custom runners) **must** call
:func:`approve_new_trade_for_execution` on the trade dict **before** venue submit or
broker order placement. There is no supported bypass.

Phase 2 persistence already applies this inside
:func:`trading_ai.phase2.trade_ops.log_trade` (before ``TradeRecord`` validation).
Additional guards: :func:`validate_trade_open_invariants` with ``live=True`` runs after
approval and again immediately before Phase 2 model validation.

Exports below are the only supported public API for opening risk-sized trades.
"""

from trading_ai.automation.position_sizing_policy import (  # noqa: F401
    TradePlacementBlocked,
    approve_new_trade_for_execution,
    normalize_position_sizing_meta,
    validate_trade_open_invariants,
)

__all__ = [
    "TradePlacementBlocked",
    "approve_new_trade_for_execution",
    "normalize_position_sizing_meta",
    "validate_trade_open_invariants",
]
