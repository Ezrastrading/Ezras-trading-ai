"""
Live default: adaptive gate split + logging. Falls back to static split with explicit ``allocation_source``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.deployment.deployment_models import iso_now
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.shark.coinbase_spot.capital_allocation import compute_gate_allocation_split

logger = logging.getLogger(__name__)

_PROOF_SOURCE = "trading_ai.control.adaptive_routing_live:compute_live_gate_allocation"


def adaptive_routing_proof_path() -> Path:
    p = ezras_runtime_root() / "data" / "control" / "adaptive_routing_proof.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def compute_live_gate_allocation(
    *,
    aos_report: Optional[Dict[str, Any]] = None,
    market_quality_allows_adaptive: bool = True,
    entrypoint: str = "compute_live_gate_allocation",
    route: str = "coinbase_nte_sizing",
    venue: str = "coinbase",
    product_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns gate fractions, ``allocation_source`` (adaptive_route | fallback_static_route), and writes proof.

    Always persists ``adaptive_routing_proof.json`` — including static fallback — with ``proof_source``.
    """
    static = compute_gate_allocation_split()
    allocation_source = "fallback_static_route"
    fallback_reason: Optional[str] = None
    conf = 0.55
    ga = float(static.gate_a)
    gb = float(static.gate_b)

    rec: Dict[str, Any] = {}
    edge_alloc: Dict[str, Any] = {}

    if aos_report and market_quality_allows_adaptive:
        rec = aos_report.get("recommended_gate_allocations") or {}
        if isinstance(rec, dict) and rec.get("gate_a") is not None and rec.get("gate_b") is not None:
            try:
                ga = float(rec["gate_a"])
                gb = float(rec["gate_b"])
                s = ga + gb
                if s > 0:
                    ga, gb = ga / s, gb / s
                allocation_source = "adaptive_route"
                conf = 0.82 if aos_report.get("confidence_scaling_ready") else 0.68
                edge_alloc = aos_report.get("recommended_edge_allocations") or {}
                if not isinstance(edge_alloc, dict):
                    edge_alloc = {}
            except (TypeError, ValueError):
                rec = {}
                fallback_reason = "recommended_gate_allocations_invalid"
        else:
            fallback_reason = "missing_or_incomplete_recommended_gate_allocations_in_aos_report"
    else:
        if not aos_report:
            fallback_reason = "aos_report_missing"
        elif not market_quality_allows_adaptive:
            fallback_reason = "market_quality_disallows_adaptive_routing"

    recommended_gate_allocations = {"gate_a": float(ga), "gate_b": float(gb)}
    out = {
        "generated_at": iso_now(),
        "ts": time.time(),
        "route": route,
        "entrypoint": entrypoint,
        "venue": venue,
        "product_id": product_id,
        "allocation_source": allocation_source,
        "route_source": allocation_source,
        "routing_confidence": conf,
        "recommended_gate_allocations": recommended_gate_allocations,
        "recommended_edge_allocations": edge_alloc if edge_alloc else _default_edge_alloc_hint(ga, gb),
        "gate_a_fraction": float(ga),
        "gate_b_fraction": float(gb),
        "static_fallback_equivalent": {"gate_a": static.gate_a, "gate_b": static.gate_b},
        "fallback_reason": fallback_reason,
        "adaptive_inputs_used": {
            "market_quality_allows_adaptive": bool(market_quality_allows_adaptive),
            "had_aos_recommended_gate_allocations": bool(rec) and allocation_source == "adaptive_route",
        },
        "proof_source": _PROOF_SOURCE,
        "proof_kind": "real_runtime_path",
    }
    try:
        adaptive_routing_proof_path().write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("adaptive_routing_proof write: %s", exc)
    return out


def _default_edge_alloc_hint(ga: float, gb: float) -> Dict[str, Any]:
    """Informational split when AOS does not supply edge buckets."""
    return {
        "note": "default_lane_split_from_gate_fractions",
        "primary_lane_weight": round(ga, 4),
        "secondary_lane_weight": round(gb, 4),
    }


def apply_allocation_to_usd_notional(
    base_usd: float,
    *,
    for_gate: str,
    allocation: Dict[str, Any],
) -> Tuple[float, Dict[str, Any]]:
    """Scale notionals for Gate A vs Gate B lane (informational for logging)."""
    g = (for_gate or "gate_a").lower()
    frac = float(allocation.get("gate_a_fraction") if g == "gate_a" else allocation.get("gate_b_fraction") or 0.5)
    adj = max(0.0, float(base_usd) * frac)
    meta = {"base_usd": base_usd, "gate": g, "fraction_used": frac, "adjusted_usd": adj}
    return adj, meta
