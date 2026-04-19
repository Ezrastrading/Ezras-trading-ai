"""Capital weights from expectancy, stability, and drawdown — validated edges only."""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Mapping, Sequence

from trading_ai.edge.models import EdgeStatus
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.edge.scoring import compute_edge_metrics


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def allocation_weights_for_validated(
    registry: EdgeRegistry,
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Returns normalized weights over edges in ``validated`` or ``scaled`` status.
    More capital → higher post_fee_expectancy and stability; penalize drawdown.
    """
    edges = [e for e in registry.list_edges() if e.status in (EdgeStatus.VALIDATED.value, EdgeStatus.SCALED.value)]
    scores: List[float] = []
    detail: List[Dict[str, Any]] = []
    for e in edges:
        m = compute_edge_metrics(events, e.edge_id)
        dd = max(m.max_drawdown, 1e-9)
        # Raw score: positive expectancy, higher stability, lower drawdown
        raw = max(0.0, m.post_fee_expectancy) * max(0.0, m.stability_score) / (1.0 + math.log1p(dd))
        scores.append(raw)
        detail.append(
            {
                "edge_id": e.edge_id,
                "status": e.status,
                "raw_score": raw,
                "post_fee_expectancy": m.post_fee_expectancy,
                "stability_score": m.stability_score,
                "max_drawdown": m.max_drawdown,
                "total_trades": m.total_trades,
            }
        )
    ssum = sum(scores) or 0.0
    weights: Dict[str, float] = {}
    if ssum <= 0.0 and detail:
        # Equal split if no positive signal (still not assuming profitability)
        eq = 1.0 / len(detail)
        for d in detail:
            weights[str(d["edge_id"])] = eq
    else:
        for d, sc in zip(detail, scores):
            weights[str(d["edge_id"])] = (sc / ssum) if ssum > 0 else 0.0

    testing_fraction = _env_float("EDGE_TESTING_CAPITAL_FRACTION", 0.05)

    return {
        "weights": weights,
        "detail": detail,
        "testing_reserved_fraction": testing_fraction,
        "validated_pool_fraction": max(0.0, 1.0 - testing_fraction),
    }


def size_scale_for_edge(
    edge_id: str,
    *,
    base_fraction: float,
    registry: EdgeRegistry,
    events: Sequence[Mapping[str, Any]],
) -> float:
    """Multiply a base notional fraction by normalized allocation weight."""
    aw = allocation_weights_for_validated(registry, events)
    w = aw["weights"].get(edge_id)
    if w is None:
        return base_fraction * _env_float("EDGE_TESTING_SIZE_MULTIPLIER", 0.1)
    pool = float(aw["validated_pool_fraction"])
    return base_fraction * pool * w
