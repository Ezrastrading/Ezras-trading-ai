"""Explicit order lifecycle states for Coinbase (user stream + REST reconciliation)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class OrderState(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FULLY_FILLED = "fully_filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXIT_SENT = "exit_sent"
    EXIT_CONFIRMED = "exit_confirmed"
    FAILED = "failed"


_TERMINAL = {
    OrderState.FULLY_FILLED,
    OrderState.CANCELED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
    OrderState.EXIT_CONFIRMED,
    OrderState.FAILED,
}


def transition(
    current: OrderState,
    event: str,
    *,
    filled_ratio: Optional[float] = None,
) -> OrderState:
    """Conservative transitions from user-stream / REST events."""
    ev = (event or "").strip().lower()
    if current in _TERMINAL and ev not in ("noop", "reconcile"):
        return current
    if ev in ("create", "client_create"):
        return OrderState.CREATED
    if ev in ("submit", "pending"):
        return OrderState.SUBMITTED
    if ev in ("ack", "received", "open"):
        return OrderState.OPEN if current == OrderState.ACKNOWLEDGED else OrderState.ACKNOWLEDGED
    if ev in ("open_resting",):
        return OrderState.OPEN
    if ev in ("partial", "partial_fill"):
        return OrderState.PARTIALLY_FILLED
    if ev in ("fill", "done", "filled", "filled_complete"):
        if filled_ratio is not None and filled_ratio < 1.0:
            return OrderState.PARTIALLY_FILLED
        return OrderState.FULLY_FILLED
    if ev in ("cancel_pending", "cancel_requested"):
        return OrderState.CANCEL_REQUESTED
    if ev in ("canceled", "cancelled"):
        return OrderState.CANCELED
    if ev in ("reject", "rejected"):
        return OrderState.REJECTED
    if ev in ("expire", "expired"):
        return OrderState.EXPIRED
    if ev in ("exit_send", "exit_sent"):
        return OrderState.EXIT_SENT
    if ev in ("exit_confirm", "exit_confirmed"):
        return OrderState.EXIT_CONFIRMED
    if ev in ("fail", "failed"):
        return OrderState.FAILED
    return current


def blank_order_record(order_id: str, product_id: str) -> Dict[str, Any]:
    return {
        "order_id": order_id,
        "product_id": product_id,
        "state": OrderState.CREATED.value,
        "filled_base": 0.0,
        "updated_ts": 0.0,
    }
