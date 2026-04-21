"""Honesty helpers for Gate A row provenance."""

from __future__ import annotations

from typing import Any, Dict, Mapping


def liquidity_stability_provenance_for_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    liq = float(row.get("liquidity_score") or 0.0)
    spread = row.get("spread_bps")
    conf = 0.9 if spread is not None and liq > 0.5 else 0.35
    return {
        "product_id": row.get("product_id"),
        "liquidity_truth_confidence": conf,
        "spread_bps": spread,
    }
