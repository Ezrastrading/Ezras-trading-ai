"""Core types for spot routing — real products and explicit multi-leg routes (no synthetic symbols)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class SpotProductRef:
    """One Coinbase Advanced Trade **real** product id (single market)."""

    product_id: str
    base_asset: str = ""
    quote_asset: str = ""


@dataclass
class Route:
    """
    An executable path: ordered list of **real** product ids (each leg is one market).

    Example: convert USDC → BTC using one leg: ``[SpotProductRef("BTC-USDC")]``.
    Multi-leg (e.g. USDC → USD → BTC) is ``["USDC-USD", "BTC-USD"]`` only when each product exists
    and policy allows — search/planning lives in future modules; max legs default 2.
    """

    legs: Tuple[SpotProductRef, ...] = field(default_factory=tuple)
    max_legs: int = 2

    def as_product_ids(self) -> List[str]:
        return [x.product_id for x in self.legs]


def route_quality_stub() -> Dict[str, Any]:
    """Placeholder for liquidity / fee / complexity scoring (future)."""
    return {
        "liquidity_score": None,
        "fee_cost_bps": None,
        "expected_slippage_bps": None,
        "route_quality_score": None,
        "note": "scoring_not_implemented_v1",
    }
