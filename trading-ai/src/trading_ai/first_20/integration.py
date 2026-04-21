"""Public entry points — post-trade, loop-proof rebuy, activation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.first_20.engine import activate_diagnostic_phase, process_closed_trade


def maybe_process_first_20_closed_trade(
    trade: Dict[str, Any],
    post_trade_out: Optional[Dict[str, Any]] = None,
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Called after a validated closed trade. Merges ``trade["first_20"]`` if present.
    Does not raise.
    """
    extra: Dict[str, Any] = {}
    if isinstance(trade.get("first_20"), dict):
        extra.update(trade["first_20"])
    return process_closed_trade(trade, post_trade_out, runtime_root=runtime_root, extra=extra if extra else None)


def on_universal_loop_proof_written(
    proof_payload: Dict[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> None:
    """Hook from universal execution loop proof — rebuy audit only (never raises)."""
    try:
        from trading_ai.first_20.constants import P_TRUTH, PhaseStatus
        from trading_ai.first_20.rebuy import record_rebuy_evaluation
        from trading_ai.first_20.storage import read_json

        truth = read_json(P_TRUTH, runtime_root=runtime_root) or {}
        if str(truth.get("phase_status")) not in (
            PhaseStatus.ACTIVE_DIAGNOSTIC.value,
            PhaseStatus.PAUSED_REVIEW_REQUIRED.value,
        ):
            return
        ready = bool(proof_payload.get("ready_for_rebuy"))
        reason = str(proof_payload.get("rebuy_policy_reason") or proof_payload.get("blocking_reason_if_any") or "")
        lifecycle = proof_payload.get("lifecycle_stages") or {}
        any_before_log = bool(lifecycle.get("entry_fill_confirmed")) and not bool(lifecycle.get("local_write_ok"))
        any_before_exit = bool(lifecycle.get("entry_fill_confirmed")) and not bool(lifecycle.get("exit_fill_confirmed"))
        record_rebuy_evaluation(
            rebuy_allowed=ready,
            block_reason=reason if not ready else None,
            any_attempt=True,
            any_before_log_completion=any_before_log,
            any_before_exit_truth=any_before_exit,
            runtime_root=runtime_root,
        )
    except Exception:
        return


__all__ = [
    "activate_diagnostic_phase",
    "maybe_process_first_20_closed_trade",
    "on_universal_loop_proof_written",
    "process_closed_trade",
]
