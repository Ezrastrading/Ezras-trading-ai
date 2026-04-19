"""
Canonical spot position / PnL from explicit base-sized fills.

Never infers base from quote; all position deltas use fill.base_size.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union


class FillSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class PositionState:
    asset: str
    base_size: float = 0.0
    """Position size in base asset units (e.g. BTC), always from fills."""
    quote_spent: float = 0.0
    """USD cost basis for remaining long (buy notional + buy-side fees allocated to inventory)."""
    avg_entry_price: float = 0.0
    """quote_spent / base_size when base_size > 0."""
    realized_pnl: float = 0.0
    """Cumulative realized PnL in USD including fee effects on closed size."""
    fees_paid: float = 0.0
    """All fees paid (buy + sell) attributed to this position path."""


@dataclass
class Fill:
    """One execution report; base_size is authoritative — never derived from quote."""

    side: Union[str, FillSide]
    base_size: float
    quote_size: float
    """Notional quote leg (e.g. USD) for this fill: typically price * base_size."""
    price: float
    """VWAP or last price for this fill (informational; PnL uses avg_entry vs price)."""
    fee: float
    """Fee charged on this fill in USD (non-negative)."""


def _side(s: Union[str, FillSide]) -> FillSide:
    if isinstance(s, FillSide):
        return s
    u = str(s).strip().upper()
    if u in ("BUY", "B"):
        return FillSide.BUY
    if u in ("SELL", "S"):
        return FillSide.SELL
    raise ValueError(f"unknown fill side: {s!r}")


def update_position_from_fill(position: PositionState, fill: Fill) -> None:
    """
    Apply a fill to ``position`` in place.

    BUY: grow inventory; ``quote_spent`` increases by notional plus fee; recompute average entry.
    SELL: shrink inventory; realize PnL vs average entry; fees reduce realized PnL.
    """
    if fill.base_size <= 0:
        return
    side = _side(fill.side)
    fee = max(0.0, float(fill.fee))

    if side == FillSide.BUY:
        position.base_size += float(fill.base_size)
        position.quote_spent += float(fill.quote_size) + fee
        position.fees_paid += fee
        if position.base_size > 0:
            position.avg_entry_price = position.quote_spent / position.base_size
        return

    # SELL
    sold = min(float(fill.base_size), position.base_size)
    if sold <= 0:
        return
    avg = float(position.avg_entry_price)
    px = float(fill.price)
    # PnL vs entry, net of sell-side fee (buy fees already embedded in avg via quote_spent)
    pnl = (px - avg) * sold - fee
    position.realized_pnl += pnl
    position.fees_paid += fee
    position.base_size -= sold
    # Release cost basis of sold units at average entry
    position.quote_spent -= avg * sold
    if position.base_size <= 1e-18:
        position.base_size = 0.0
        position.quote_spent = 0.0
        position.avg_entry_price = 0.0
    else:
        position.avg_entry_price = position.quote_spent / position.base_size


def compute_unrealized_pnl(position: PositionState, current_price: float) -> float:
    """Mark-to-market on remaining base (exit fees not applied)."""
    if position.base_size <= 0:
        return 0.0
    return (float(current_price) - float(position.avg_entry_price)) * float(position.base_size)


def compute_total_pnl(position: PositionState, current_price: float) -> float:
    """Realized + unrealized on remaining inventory."""
    return float(position.realized_pnl) + compute_unrealized_pnl(position, current_price)


def position_state_from_open_dict(pos: dict) -> PositionState:
    """Map NTE / execution open position dict into a :class:`PositionState`."""
    pid = str(pos.get("product_id") or pos.get("asset") or "")
    base = float(pos.get("base_size") or 0.0)
    buy_cost = float(pos.get("buy_cost_usd") or 0.0)
    entry = float(pos.get("entry_price") or 0.0)
    buy_fee = float(pos.get("buy_fee_usd") or 0.0)
    if buy_cost > 0 and base > 0:
        qs = buy_cost
        avg = qs / base
    elif entry > 0 and base > 0:
        qs = entry * base + buy_fee
        avg = qs / base
    else:
        qs = 0.0
        avg = 0.0
    return PositionState(
        asset=pid,
        base_size=base,
        quote_spent=qs,
        avg_entry_price=avg,
        realized_pnl=0.0,
        fees_paid=buy_fee,
    )


def net_exit_from_fills(
    position: PositionState,
    *,
    sell_quote_notional: float,
    sell_fee: float,
    sold_base: float,
) -> float:
    """
    Apply one aggregated SELL fill (e.g. all exit fills) and return net USD PnL for the exit.

    Compatible with exchange reports that sum price*size into notional and fees separately.
    """
    if sold_base <= 0:
        return 0.0
    px = float(sell_quote_notional) / float(sold_base)
    f = Fill(
        side=FillSide.SELL,
        base_size=sold_base,
        quote_size=float(sell_quote_notional),
        price=px,
        fee=float(sell_fee),
    )
    r0 = float(position.realized_pnl)
    update_position_from_fill(position, f)
    return float(position.realized_pnl) - r0
