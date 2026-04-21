"""
Rebuy / next-opportunity gating — unresolved round-trips block new entries.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple


class TerminalHonestState(str, Enum):
    ROUND_TRIP_SUCCESS = "round_trip_success"
    ENTRY_FAILED_PRE_FILL = "entry_failed_pre_fill"
    ENTRY_FILLED_EXIT_FAILED = "entry_filled_exit_failed"
    ROUND_TRIP_PARTIAL_FAILURE = "round_trip_partial_failure"
    RISK_HALTED_AFTER_TRADE = "risk_halted_after_trade"
    GOVERNANCE_BLOCKED = "governance_blocked"
    DUPLICATE_BLOCKED = "duplicate_blocked"
    VENUE_REJECTED = "venue_rejected"
    ADAPTIVE_BRAKE_BLOCKED = "adaptive_brake_blocked"
    UNRESOLVED_IN_FLIGHT = "unresolved_in_flight"


def _bget(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def can_open_next_trade_after(previous: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    Returns (allowed, reason).

    If ``previous`` is None or empty → allowed (no prior in-flight trade in this context).
    """
    if not previous:
        return True, "no_prior_trade_context"

    terminal = str(previous.get("terminal_honest_state") or previous.get("terminal_state") or "").strip()
    if terminal == TerminalHonestState.UNRESOLVED_IN_FLIGHT.value:
        return False, "prior_trade_unresolved_in_flight"

    # Universal proof / contract shape
    entry_fill = bool(previous.get("entry_fill_confirmed") or _bget(previous, "stages", "STAGE_3_ENTRY_FILL_CONFIRMED", "ok"))
    exit_fill = bool(previous.get("exit_fill_confirmed") or _bget(previous, "stages", "STAGE_5_EXIT_FILL_CONFIRMED", "ok"))
    local_ok = bool(previous.get("local_write_ok") or _bget(previous, "stages", "STAGE_7_LOCAL_DATA_WRITTEN", "ok"))
    pnl_ok = bool(previous.get("pnl_verified") or _bget(previous, "stages", "STAGE_6_PNL_VERIFIED", "ok"))

    if entry_fill and not exit_fill:
        return False, "entry_filled_exit_not_verified_rebuy_blocked"

    if previous.get("final_execution_proven") is True:
        if not (entry_fill and exit_fill and pnl_ok and local_ok):
            return False, "final_execution_proven_inconsistent_with_stage_flags"
        return True, "prior_round_trip_success_and_logged"

    if terminal == TerminalHonestState.ROUND_TRIP_SUCCESS.value:
        return True, "terminal_round_trip_success"

    if terminal in (
        TerminalHonestState.ENTRY_FAILED_PRE_FILL.value,
        TerminalHonestState.VENUE_REJECTED.value,
        TerminalHonestState.DUPLICATE_BLOCKED.value,
        TerminalHonestState.ADAPTIVE_BRAKE_BLOCKED.value,
    ):
        # Failure logged — safe to scan next; adapter must have persisted proof
        return True, f"terminal_{terminal}_failure_logged_next_scan_allowed"

    if terminal == TerminalHonestState.ENTRY_FILLED_EXIT_FAILED.value:
        return False, "entry_filled_exit_failed_do_not_rebuy_until_resolved"

    if terminal == TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE.value:
        if local_ok:
            return True, "partial_failure_but_local_truth_logged"
        return False, "partial_failure_local_write_incomplete"

    if previous.get("halt_after_trade") or previous.get("risk_halted_after_trade"):
        return False, "risk_halted_after_trade"

    if previous.get("governance_blocked"):
        return False, "governance_blocked"

    # Default conservative: if we have any hint of in-flight entry without terminal
    if previous.get("entry_order_submitted") and not entry_fill:
        return False, "entry_submitted_not_confirmed"

    return True, "no_blocking_signal_default_allow_scan"
