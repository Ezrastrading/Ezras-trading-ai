"""Exchange vs internal position truth — drift raises before capital damage propagates."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _tol() -> float:
    try:
        return float((os.environ.get("RECONCILE_BASE_TOLERANCE") or "1e-6").strip() or "1e-6")
    except ValueError:
        return 1e-6


def reconcile_position(
    local_position: Any,
    exchange_balance: Any,
    *,
    tolerance: Optional[float] = None,
) -> None:
    """
    Compare internal base position to exchange-reported balance.

    ``local_position`` may be a float (base size) or mapping with ``base_size``.
    ``exchange_balance`` is the authoritative base balance on the venue.
    """
    tol = float(tolerance) if tolerance is not None else _tol()
    if isinstance(local_position, Mapping):
        lb = float(local_position.get("base_size") or local_position.get("base") or 0)
    else:
        lb = float(local_position or 0)
    eb = float(exchange_balance or 0)
    if abs(lb - eb) > tol:
        msg = f"POSITION DRIFT DETECTED: local_base={lb} exchange_base={eb} tol={tol}"
        logger.critical(msg)
        raise ValueError(msg)


def maybe_reconcile_coinbase_position(
    *,
    product_id: str,
    local_base: float,
    get_spot_balance_fn: Any,
) -> None:
    """Best-effort: compare to Coinbase available base if callable returns a float."""
    if not callable(get_spot_balance_fn):
        return
    try:
        ex = float(get_spot_balance_fn(product_id))
    except Exception:
        return
    reconcile_position(local_base, ex)
