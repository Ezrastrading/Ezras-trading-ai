"""Automated edge evolution loop — measure net outcomes, rank, route, report (no profit guarantee)."""

from trading_ai.evolution.loop import run_evolution_cycle
from trading_ai.evolution.routing import compute_adaptive_gate_split, routing_dict
from trading_ai.evolution.scoring import (
    MaturityLevel,
    knowledge_liquidity_score,
    rank_edges_by_score,
    unified_edge_score,
)
from trading_ai.evolution.safest import rank_safest_edges, safest_venue
from trading_ai.evolution.accumulation import accumulation_snapshot, contribution_by_dimension
from trading_ai.evolution.acceleration import evaluate_goal_acceleration
from trading_ai.evolution.adjustments import apply_automated_adjustments, suggest_adjustments

__all__ = [
    "run_evolution_cycle",
    "compute_adaptive_gate_split",
    "routing_dict",
    "MaturityLevel",
    "knowledge_liquidity_score",
    "rank_edges_by_score",
    "unified_edge_score",
    "rank_safest_edges",
    "safest_venue",
    "accumulation_snapshot",
    "contribution_by_dimension",
    "evaluate_goal_acceleration",
    "apply_automated_adjustments",
    "suggest_adjustments",
]
