"""Canonical execution failure codes — every failure maps to exactly one."""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ExecutionErrorCode(str, Enum):
    RUNTIME_POLICY_DISALLOWS_FUNDABLE_PRODUCT = "runtime_policy_disallows_fundable_product"
    INSUFFICIENT_ALLOWED_QUOTE_BALANCE = "insufficient_allowed_quote_balance"
    VENUE_MIN_NOTIONAL_NOT_FUNDABLE = "venue_min_notional_not_fundable"
    GOVERNANCE_BLOCKED = "governance_blocked"
    TICKER_UNAVAILABLE = "ticker_unavailable"
    EXECUTION_TIMEOUT = "execution_timeout"
    PARTIAL_FILL_FAILURE = "partial_fill_failure"
    RECONCILIATION_FAILURE = "reconciliation_failure"
    UNKNOWN_EXECUTION_FAILURE = "unknown_execution_failure"
    # Extended operational codes
    SYSTEM_KILL_SWITCH_ACTIVE = "system_kill_switch_active"
    FAILSAFE_HALTED = "failsafe_halted"
    MAX_DAILY_LOSS_EXCEEDED = "max_daily_loss_exceeded"
    MAX_SESSION_LOSS_EXCEEDED = "max_session_loss_exceeded"
    MAX_POSITION_LIMIT_EXCEEDED = "max_position_limit_exceeded"
    DUPLICATE_TRADE_GUARD = "duplicate_trade_guard"
    FAILED_STREAK_HALT = "failed_streak_halt"
    CAPITAL_TRUTH_MISMATCH = "capital_truth_mismatch"
    MULTI_LEG_EXECUTION_NOT_ENABLED = "multi_leg_execution_not_enabled"
    GATE_OR_LOCK_BLOCKED = "gate_or_lock_blocked"


def normalize_error_code(raw: Optional[str]) -> str:
    """
    Map arbitrary reason strings to a single canonical code when possible.
    Unknown strings become ``unknown_execution_failure`` unless already a known enum value.
    """
    if not raw:
        return ExecutionErrorCode.UNKNOWN_EXECUTION_FAILURE.value
    s = str(raw).strip().lower()
    # Direct enum match
    for m in ExecutionErrorCode:
        if s == m.value.lower():
            return m.value
    # Common legacy substrings
    if "kill_switch" in s or "system_kill_switch" in s:
        return ExecutionErrorCode.SYSTEM_KILL_SWITCH_ACTIVE.value
    if "governance" in s and "block" in s:
        return ExecutionErrorCode.GOVERNANCE_BLOCKED.value
    if "product_not_allowed" in s or "runtime_policy" in s:
        return ExecutionErrorCode.RUNTIME_POLICY_DISALLOWS_FUNDABLE_PRODUCT.value
    if "quote_below_min" in s or "min_notional" in s:
        return ExecutionErrorCode.VENUE_MIN_NOTIONAL_NOT_FUNDABLE.value
    if "quote" in s and ("insufficient" in s or "balance" in s):
        return ExecutionErrorCode.INSUFFICIENT_ALLOWED_QUOTE_BALANCE.value
    if "timeout" in s:
        return ExecutionErrorCode.EXECUTION_TIMEOUT.value
    if "partial" in s and "fill" in s:
        return ExecutionErrorCode.PARTIAL_FILL_FAILURE.value
    if "reconcil" in s:
        return ExecutionErrorCode.RECONCILIATION_FAILURE.value
    if "ticker" in s and "unavailable" in s:
        return ExecutionErrorCode.TICKER_UNAVAILABLE.value
    if "multi" in s and "leg" in s:
        return ExecutionErrorCode.MULTI_LEG_EXECUTION_NOT_ENABLED.value
    return ExecutionErrorCode.UNKNOWN_EXECUTION_FAILURE.value
