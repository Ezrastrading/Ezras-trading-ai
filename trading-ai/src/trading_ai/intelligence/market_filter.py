"""Spread and liquidity sanity checks."""

from typing import Optional, Tuple


def passes_market_conditions(
    bid: float,
    ask: float,
    mid_price: float,
    liquidity: float,
    trade_size: float,
) -> Tuple[bool, Optional[str]]:
    spread = ask - bid
    if mid_price <= 0:
        return False, "invalid_mid"
    spread_pct = spread / mid_price
    if spread_pct > 0.002:
        return False, "spread_too_wide"
    if liquidity < trade_size * 3:
        return False, "low_liquidity"
    return True, None
