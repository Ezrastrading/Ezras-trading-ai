"""Deployment enforcement — pre/post trade invariants; halts when ``EZRAS_DEPLOYMENT_ENFORCEMENT`` is set."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Union

TradeLike = Union[Mapping[str, Any], Any]
FillLike = Union[Mapping[str, Any], Any]


def deployment_enforcement_enabled() -> bool:
    return (os.environ.get("EZRAS_DEPLOYMENT_ENFORCEMENT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def deployment_scaling_enforced() -> bool:
    return (os.environ.get("EZRAS_BLOCK_SCALING_UNTIL_DEPLOYMENT_READY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def assert_trade_lifecycle_complete(
    *,
    buy_success: bool,
    sell_success: bool,
    pnl_computed: bool,
    supabase_written: bool,
) -> None:
    trade_complete = buy_success and sell_success and pnl_computed and supabase_written
    if not trade_complete:
        raise RuntimeError("TRADE INCOMPLETE — BLOCK SYSTEM")


@dataclass
class FillSnapshot:
    base_size: float
    quote_size: float
    price: float


@dataclass
class PositionSnapshot:
    base_size: float


class DeploymentGuard:
    def validate_pre_trade(self, trade: TradeLike) -> None:
        if isinstance(trade, Mapping):
            q = float(trade.get("quote_size") or trade.get("notional_usd") or 0)
            p = float(trade.get("price") or 0)
        else:
            q = float(getattr(trade, "quote_size", None) or getattr(trade, "notional_usd", None) or 0)
            p = float(getattr(trade, "price", None) or 0)
        assert q > 0, "Invalid quote size"
        assert p > 0, "Invalid price"

    def validate_post_buy(self, fill: Union[FillSnapshot, Mapping[str, Any]]) -> None:
        if isinstance(fill, Mapping):
            base = float(fill.get("base_size") or 0)
            quote = float(fill.get("quote_size") or 0)
            price = float(fill.get("price") or 0)
        else:
            base = float(fill.base_size)
            quote = float(fill.quote_size)
            price = float(fill.price)
        if price <= 0 or quote <= 0:
            raise ValueError("BUY SIZE MISMATCH: non-positive quote or price")
        expected_base = quote / price
        if expected_base <= 0:
            raise ValueError("BUY SIZE MISMATCH")
        rel = abs(base - expected_base) / expected_base
        if rel > 0.02:
            raise ValueError("BUY SIZE MISMATCH")

    def validate_post_sell(self, position: Union[PositionSnapshot, Mapping[str, Any]], sell_size: float) -> None:
        if isinstance(position, Mapping):
            pos_base = float(position.get("base_size") or 0)
        else:
            pos_base = float(position.base_size)
        if sell_size > pos_base + 1e-12:
            raise ValueError("OVERSELL DETECTED")

    def validate_round_trip(self, buy_fill: Union[FillSnapshot, Mapping[str, Any]], sell_fill: Union[FillSnapshot, Mapping[str, Any]]) -> None:
        if isinstance(sell_fill, Mapping):
            sb = float(sell_fill.get("base_size") or 0)
        else:
            sb = float(sell_fill.base_size)
        if sb <= 0:
            raise ValueError("INVALID SELL FILL")
        if isinstance(buy_fill, Mapping):
            bb = float(buy_fill.get("base_size") or 0)
        else:
            bb = float(buy_fill.base_size)
        if bb <= 0:
            raise ValueError("INVALID BUY FILL")


def assert_system_not_halted() -> None:
    from trading_ai.core.system_guard import get_system_guard

    halt, reason = get_system_guard().should_shutdown()
    if halt:
        raise RuntimeError(f"SYSTEM HALTED — NO TRADING ({reason})")


def assert_position_reconciled_flat(
    *,
    client: Any,
    product_id: str,
    tolerance_base: float,
) -> None:
    """After a full exit, available base currency on the exchange should be negligible."""
    pid = (product_id or "").upper()
    cur = "BTC" if "BTC" in pid else ("ETH" if "ETH" in pid else "")
    if not cur:
        return
    try:
        bal = float(client.get_available_balance(cur))
    except Exception as exc:
        if deployment_enforcement_enabled():
            raise RuntimeError(f"POSITION DRIFT — HALT: balance fetch failed: {exc}") from exc
        return
    if bal > float(tolerance_base):
        raise RuntimeError(f"POSITION DRIFT — HALT: {cur} available={bal} exceeds tolerance={tolerance_base}")
