"""
Operational definitions for spot crypto inventory vs quote cash — for engines, reports, and operators.

This is not execution code; it encodes invariants the trading system must respect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class SpotPair:
    """e.g. BTC-USD: base=BTC, quote=USD."""

    product_id: str
    base: str
    quote: str


EXAMPLES: Tuple[str, ...] = (
    "0.001 BTC is one thousandth of a bitcoin; 0.01 BTC is 10x 0.001 BTC — always compare quantities in base units.",
    "BTC-USD and ETH-USD share USD as quote but have different base tick sizes, volatility, and liquidity.",
    "Quote balance (USD/USDC) funds BUY orders; SELL releases quote and consumes base inventory.",
    "Five percent of spot price is a ratio; dollar PnL also depends on how many base units you hold.",
)


def parse_pair(product_id: str) -> SpotPair:
    s = product_id.strip().upper()
    if "-" not in s:
        return SpotPair(s, "BTC", "USD")
    a, b = s.split("-", 1)
    return SpotPair(s, a.strip(), b.strip())


def spot_equity_usd(
    *,
    quote_usd: float,
    quote_usdc: float,
    base_qty: float,
    mark_usd_per_base: float,
) -> float:
    """Mark-to-market: quote cash + base notional at mark (fees not deducted)."""
    q = float(quote_usd) + float(quote_usdc)
    return q + max(0.0, float(base_qty)) * max(0.0, float(mark_usd_per_base))


def realized_pnl_sell_usd(
    *,
    avg_entry_usd_per_base: float,
    sell_price_usd_per_base: float,
    sold_base_qty: float,
    fee_usd: float,
) -> float:
    """Classic spot: (exit - entry) * qty - fee on the exit (and buy fees already in basis)."""
    return (float(sell_price_usd_per_base) - float(avg_entry_usd_per_base)) * float(sold_base_qty) - max(
        0.0, float(fee_usd)
    )


def unrealized_pnl_usd(*, mark: float, avg_entry: float, base_qty: float) -> float:
    return (float(mark) - float(avg_entry)) * max(0.0, float(base_qty))


def as_operator_card(product_id: str) -> Dict[str, Any]:
    """Structured one-screen summary for logs / JSON."""
    p = parse_pair(product_id)
    return {
        "product_id": p.product_id,
        "base_asset": p.base,
        "quote_asset": p.quote,
        "buy_uses": "quote_balance",
        "sell_uses": "base_inventory_qty",
        "quantity_is_always": "base_units_not_usd",
        "examples": list(EXAMPLES),
    }
