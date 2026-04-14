"""Registered outlet fetchers — Polymarket, Kalshi, Manifold, Metaculus, optional Coinbase/Robinhood."""

from __future__ import annotations

import os

from trading_ai.shark.outlets.kalshi import KalshiFetcher
from trading_ai.shark.outlets.manifold import ManifoldFetcher
from trading_ai.shark.outlets.metaculus import MetaculusFetcher
from trading_ai.shark.outlets.polymarket import PolymarketFetcher


def _env_truthy(name: str, default: str = "false") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def default_fetchers():
    out = [
        PolymarketFetcher(),
        KalshiFetcher(),
        ManifoldFetcher(),
        MetaculusFetcher(),
    ]
    if _env_truthy("STRATEGY_CRYPTO_ENABLED", "false"):
        from trading_ai.shark.outlets.coinbase import CoinbaseFetcher

        out.append(CoinbaseFetcher())
    if _env_truthy("STRATEGY_STOCKS_ENABLED", "false"):
        from trading_ai.shark.outlets.robinhood import RobinhoodFetcher

        out.append(RobinhoodFetcher())
    return out


__all__ = [
    "PolymarketFetcher",
    "KalshiFetcher",
    "ManifoldFetcher",
    "MetaculusFetcher",
    "default_fetchers",
]
