"""Instrument / contract structural facets for domain JSON (evidence-backed updates only)."""

from __future__ import annotations

from typing import Any, Dict

# Template keys Part 6 — filled honestly by updater from evidence.
INSTRUMENT_STRUCT_KEYS: tuple[str, ...] = (
    "what_is_traded",
    "how_price_is_formed",
    "what_causes_edge",
    "what_destroys_edge",
    "typical_liquidity_conditions",
    "typical_latency_importance",
    "fill_risk_profile",
    "spread_risk_profile",
    "event_risk_profile",
    "regime_sensitivity",
    "common_traps",
    "suitable_strategies",
    "unsuitable_strategies",
    "why_one_setup_beats_another",
)


def empty_instrument_facets() -> Dict[str, Any]:
    return {k: "" for k in INSTRUMENT_STRUCT_KEYS}
