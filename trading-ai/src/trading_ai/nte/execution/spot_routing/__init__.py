"""
Coinbase spot **routing model** (foundation).

This package is the home for universal spot concepts — assets, real products (edges),
and multi-leg **routes** (ordered sequences of products). It complements the single-source
product allowlist in :mod:`trading_ai.nte.hardening.coinbase_product_policy`.

Validation and live execution continue to share the same NTE ``products`` tuple; multi-leg
routing and portfolio-wide capital accounting are built here incrementally without
validation-only bypasses.
"""

from trading_ai.nte.execution.spot_routing.portfolio import build_wallet_inventory_rows
from trading_ai.nte.execution.spot_routing.types import Route, SpotProductRef, route_quality_stub

__all__ = ["Route", "SpotProductRef", "route_quality_stub", "build_wallet_inventory_rows"]
