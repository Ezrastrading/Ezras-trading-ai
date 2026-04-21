"""Explainable route scoring — liquidity proxy, leg penalty, health."""

from __future__ import annotations

import math
from typing import Any, Dict, List

from trading_ai.nte.execution.routing.core.product_graph import SpotAssetGraph


def score_product_path(
    graph: SpotAssetGraph,
    product_ids: List[str],
    *,
    max_legs: int = 3,
) -> Dict[str, Any]:
    """
    Higher score = better. Uses log liquidity proxy and leg-count penalty.
    """
    if not product_ids:
        return {"total_score": 1.0, "components": {"empty_path": 1.0}}
    liq_scores: List[float] = []
    health_fail = False
    for pid in product_ids:
        e = graph.edges_by_product.get(pid.upper())
        if not e:
            return {"total_score": 0.0, "components": {"missing_edge": pid}, "reject": True}
        if not e.healthy:
            health_fail = True
        v = max(1.0, float(e.liquidity_proxy or 0.0))
        liq_scores.append(math.log10(v))
    leg_penalty = 0.12 * (len(product_ids) - 1)
    liq_mean = sum(liq_scores) / max(1, len(liq_scores))
    total = max(0.0, liq_mean - leg_penalty - (0.25 if health_fail else 0.0))
    return {
        "total_score": round(total, 6),
        "components": {
            "mean_log10_liquidity_proxy": round(liq_mean, 6),
            "leg_penalty": leg_penalty,
            "health_penalty": 0.25 if health_fail else 0.0,
            "leg_count": len(product_ids),
        },
        "reject": health_fail and len(product_ids) > 1,
    }


def rank_paths(
    graph: SpotAssetGraph,
    paths: List[List[str]],
    *,
    min_score: float = 0.0,
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for p in paths:
        sc = score_product_path(graph, p)
        if sc.get("reject") or float(sc.get("total_score") or 0) < min_score:
            continue
        ranked.append({"product_path": p, "score": sc})
    ranked.sort(key=lambda x: float(x["score"].get("total_score") or 0), reverse=True)
    return ranked
