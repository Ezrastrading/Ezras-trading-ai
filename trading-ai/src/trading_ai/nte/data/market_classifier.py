"""Trending / ranging / chaotic — chaotic blocks new risk."""

from __future__ import annotations

from typing import Literal

from trading_ai.nte.data.market_state import ProductMarketState

MarketRegime = Literal["trending_up", "trending_down", "ranging", "chaotic", "unknown"]


def classify_market(
    state: ProductMarketState,
    *,
    max_spread_bps: float,
    max_volatility_bps: float,
    spike_block_pct: float,
) -> MarketRegime:
    """
    Chaotic: wide spread, high short-term vol, or price dislocated from recent mean.
    """
    if state.mid_price is None or state.best_bid is None or state.best_ask is None:
        return "unknown"
    if state.spread_bps is not None and state.spread_bps > max_spread_bps:
        return "chaotic"
    vol = state.short_volatility_bps()
    if vol > max_volatility_bps:
        return "chaotic"
    mids = list(state.recent_mids)
    if len(mids) >= 20:
        ma = sum(mids[-20:]) / 20.0
        if ma > 0 and abs(state.mid_price / ma - 1.0) > spike_block_pct:
            return "chaotic"
    if len(mids) < 12:
        return "unknown"
    short = sum(mids[-12:]) / 12.0
    long = sum(mids[-60:]) / min(60, len(mids)) if len(mids) >= 20 else short
    if short > long * 1.0008:
        return "trending_up"
    if short < long * 0.9992:
        return "trending_down"
    return "ranging"
