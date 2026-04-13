"""Registered outlet fetchers — Polymarket, Kalshi, Manifold."""

from trading_ai.shark.outlets.kalshi import KalshiFetcher
from trading_ai.shark.outlets.manifold import ManifoldFetcher
from trading_ai.shark.outlets.polymarket import PolymarketFetcher


def default_fetchers():
    return [PolymarketFetcher(), KalshiFetcher(), ManifoldFetcher()]


__all__ = [
    "PolymarketFetcher",
    "KalshiFetcher",
    "ManifoldFetcher",
    "default_fetchers",
]
