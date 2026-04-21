"""Main-goal acceleration — evidence-bounded recommendations (not risk escalation)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from trading_ai.evolution.accumulation import goal_pace_evaluation
from trading_ai.evolution.routing import AdaptiveRoutingResult, compute_adaptive_gate_split
from trading_ai.evolution.scoring import rank_edges_by_score

from trading_ai.edge.registry import EdgeRegistry


def evaluate_goal_acceleration(
    events: List[Mapping[str, Any]],
    *,
    current_capital: float,
    ranked_edges: Optional[List[Dict[str, Any]]] = None,
    routing: Optional[AdaptiveRoutingResult] = None,
) -> Dict[str, Any]:
    """
    Answer: what deserves more allocation vs less, are we behind target, safe compounding vs growth tilt.

    Does **not** increase risk purely from urgency — combines pace with evidence quality.
    """
    pace = goal_pace_evaluation(current_capital)
    rt = routing or compute_adaptive_gate_split(events)
    edges = ranked_edges
    if edges is None:
        edges = rank_edges_by_score(EdgeRegistry().list_edges(), events)

    behind = pace.get("trajectory") in ("behind", "critical")
    top = edges[:3] if edges else []
    bottom = edges[-3:] if edges else []

    more_capital: List[str] = []
    less_capital: List[str] = []
    for row in top:
        eid = str(row.get("edge_id") or "")
        if float(row.get("unified_score") or 0.0) >= 55 and row.get("maturity") not in ("degraded", "paused"):
            more_capital.append(eid)
    for row in bottom:
        eid = str(row.get("edge_id") or "")
        if float(row.get("unified_score") or 0.0) < 40:
            less_capital.append(eid)

    growth_mode = "safe_compounding"
    if behind and rt.gate_a_score + rt.gate_b_score > 0:
        growth_mode = "emphasize_proven_edges_still_bounded"
    elif behind:
        growth_mode = "defensive_preserve_capital"

    return {
        "pace": pace,
        "routing": {
            "gate_a_share": rt.split.gate_a,
            "gate_b_share": rt.split.gate_b,
            "defensive_idle": rt.defensive_idle_fraction,
            "rationale": rt.rationale,
        },
        "more_allocation_candidates": more_capital[:8],
        "reduce_allocation_candidates": less_capital[:8],
        "growth_mode_recommendation": growth_mode,
        "notes": [
            "Urgency never bypasses governance, reconciliation, or kill-switch.",
            "When behind pace but evidence is weak, prefer defensive idle and size reduction.",
        ],
    }
