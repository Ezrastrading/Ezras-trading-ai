"""Venue-agnostic asset / route types — no venue symbols in core except as opaque strings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

AssetId = str


@dataclass(frozen=True)
class UniversalProductEdge:
    """One tradable spot pair (real exchange product)."""

    venue: str
    product_id: str
    base_asset: AssetId
    quote_asset: AssetId
    liquidity_proxy: float = 0.0
    healthy: bool = True
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteLeg:
    """Single hop: trade ``product_id`` to move value in the given ``side`` sense (buy/sell from taker POV)."""

    venue: str
    product_id: str
    side: str  # "BUY" | "SELL" — semantic for planning; execution maps per venue
    spend_asset: AssetId
    receive_asset: AssetId


@dataclass
class ExecutableRoute:
    """Ordered real legs; no synthetic product ids."""

    legs: Tuple[RouteLeg, ...]
    max_legs: int = 3

    def product_ids(self) -> List[str]:
        return [x.product_id for x in self.legs]


@dataclass
class ValidationResolution:
    """Coherent validation product resolution — no contradictory chosen+error."""

    resolution_status: str  # "success" | "blocked"
    chosen_product_id: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    diagnostics: Dict[str, Any]
