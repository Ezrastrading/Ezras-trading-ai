"""
Explicit models for trading avenues (spot, prediction, options).

Describe mechanics — no live pricing or execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal

AvenueId = Literal["coinbase", "kalshi", "options"]


@dataclass
class SpotVenueModel:
    avenue: str
    base_asset: str
    quote_asset: str
    position_size_units: float
    profit_mechanic: str


@dataclass
class PredictionVenueModel:
    avenue: str
    contract_price_cents: float
    payout_usd_per_contract: float
    yes_no_exposure: str
    profit_mechanic: str


@dataclass
class OptionsVenueModel:
    avenue: str
    premium_usd: float
    strike: float
    expiry_iso: str
    contract_multiplier: float
    intrinsic_extrinsic_note: str
    profit_mechanic: str


AVENUE_REGISTRY: Dict[AvenueId, Dict[str, Any]] = {
    "coinbase": {
        "class": "spot",
        "base_asset": "variable",
        "quote_asset": "USD",
        "position_fields": ["base_qty", "avg_entry", "avg_exit"],
        "profit_summary": "Buy lower / sell higher in quote terms; subtract fees and slippage.",
    },
    "kalshi": {
        "class": "prediction",
        "contract_price_cents": "0-100 implied probability",
        "payout": "$1 per winning contract (binary)",
        "exposure": "YES or NO contracts",
        "profit_summary": "Profit when mispriced probability vs settlement; fees reduce edge.",
    },
    "options": {
        "class": "options",
        "premium": "upfront option price",
        "strike": "exercise threshold",
        "expiry": "last trading / expiration datetime",
        "multiplier": "contracts * multiplier * underlying delta",
        "profit_summary": "Directional, vol, and time decay interplay; path dependent.",
    },
}


def describe_avenue(avenue: str) -> Dict[str, Any]:
    aid = (avenue or "").strip().lower()
    if aid in AVENUE_REGISTRY:
        return {"avenue": aid, **AVENUE_REGISTRY[aid]}  # type: ignore[arg-type]
    return {"avenue": aid, "error": "unknown_avenue", "known": list(AVENUE_REGISTRY.keys())}


def avenue_structured(avenue: str) -> Dict[str, Any]:
    """Typed summary for machine consumption."""
    d = describe_avenue(avenue)
    if "error" in d:
        return d
    aid = d["avenue"]
    if aid == "coinbase":
        m = SpotVenueModel(
            avenue=aid,
            base_asset="BASE",
            quote_asset="USD",
            position_size_units=0.0,
            profit_mechanic=d["profit_summary"],
        )
        return {"type": "spot", "fields": m.__dict__}
    if aid == "kalshi":
        m = PredictionVenueModel(
            avenue=aid,
            contract_price_cents=50.0,
            payout_usd_per_contract=1.0,
            yes_no_exposure="YES or NO",
            profit_mechanic=d["profit_summary"],
        )
        return {"type": "prediction", "fields": m.__dict__}
    if aid == "options":
        m = OptionsVenueModel(
            avenue=aid,
            premium_usd=0.0,
            strike=0.0,
            expiry_iso="",
            contract_multiplier=100.0,
            intrinsic_extrinsic_note="Intrinsic = max(0, underlying-strike) for calls; extrinsic = premium - intrinsic.",
            profit_mechanic=d["profit_summary"],
        )
        return {"type": "options", "fields": m.__dict__}
    return d
