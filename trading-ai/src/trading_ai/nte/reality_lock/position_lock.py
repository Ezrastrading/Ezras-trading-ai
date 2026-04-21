"""Position truth: no oversell; exchange vs internal reconciliation."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


DEFAULT_BASE_TOLERANCE = _env_float("REALITY_LOCK_RECON_TOLERANCE", 1e-5)


def base_currency_for_product(product_id: str) -> str:
    pid = (product_id or "").strip().upper()
    if "-" in pid:
        return pid.split("-")[0]
    return pid


def reconcile_position(exchange_balance: float, internal_position: float, *, tolerance: float) -> None:
    """Raise ``POSITION_DESYNC_ABORT`` if balances diverge beyond tolerance."""
    if abs(float(exchange_balance) - float(internal_position)) > float(tolerance):
        raise Exception("POSITION_DESYNC_ABORT")


def reconcile_coinbase_spot_base(
    client: Any,
    base_ccy: str,
    internal_open_base_sum: float,
    *,
    tolerance: float = DEFAULT_BASE_TOLERANCE,
) -> None:
    """
    Compare summed open position base (internal) to exchange available balance for that asset.

    Multi-product: pass the sum of all open positions that consume this base currency.
    """
    try:
        ex = float(client.get_available_balance(base_ccy))
    except Exception as exc:
        raise Exception(f"POSITION_DESYNC_ABORT: balance_fetch:{base_ccy}:{exc}") from exc
    reconcile_position(ex, internal_open_base_sum, tolerance=tolerance)


def assert_no_oversell_strict(current_position_base: float, sell_size: float) -> None:
    """Before any sell — hard block."""
    if sell_size > float(current_position_base) + 1e-12:
        raise Exception("OVERSOLD_BLOCKED")


def assert_post_fill_desync(
    exchange_balance: float,
    internal_position: float,
    *,
    tolerance: float = DEFAULT_BASE_TOLERANCE,
) -> None:
    """After every fill — abort if internal and venue diverge."""
    if abs(exchange_balance - internal_position) > tolerance:
        raise Exception("POSITION_DESYNC_ABORT")
