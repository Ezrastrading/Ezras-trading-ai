"""
NTE data module for Avenue A Coinbase execution.

Exports:
- FeatureSnapshot, compute_features from feature_engine
- classify_market from market_classifier
- ProductMarketState from market_state
- AdvancedTradeWSFeed from ws_advanced_trade
"""

from __future__ import annotations

from .feature_engine import FeatureSnapshot, compute_features
from .market_classifier import classify_market
from .market_state import ProductMarketState
from .ws_advanced_trade import AdvancedTradeWSFeed

__all__ = [
    "FeatureSnapshot",
    "compute_features",
    "classify_market",
    "ProductMarketState",
    "AdvancedTradeWSFeed",
]
