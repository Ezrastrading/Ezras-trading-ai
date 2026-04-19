"""Fail-fast guards: paper/replay cannot place live orders — delegates to live_order_guard."""

from __future__ import annotations

from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted_legacy
from trading_ai.nte.hardening.mode_context import ExecutionMode, get_execution_mode


def assert_live_order_permitted(operation: str) -> None:
    """Backward-compatible wrapper; prefer ``live_order_guard.assert_live_order_permitted``."""
    assert_live_order_permitted_legacy(operation)


def replay_blocks_orders(operation: str) -> None:
    if get_execution_mode() != ExecutionMode.REPLAY:
        return
    from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure

    log_failure(
        FailureClass.MODE_MISMATCH,
        f"Replay mode must not place orders: {operation}",
        severity="warning",
        metadata={"operation": operation},
    )
    raise RuntimeError("Replay mode: orders blocked")
