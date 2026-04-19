"""NTE production hardening: failure guards, freshness, mode safety, integrity."""

from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure
from trading_ai.nte.hardening.mode_context import (
    ExecutionMode,
    get_execution_mode,
    get_mode_context,
    live_orders_allowed,
)

__all__ = [
    "FailureClass",
    "log_failure",
    "ExecutionMode",
    "get_execution_mode",
    "get_mode_context",
    "live_orders_allowed",
]
