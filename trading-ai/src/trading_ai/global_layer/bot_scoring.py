"""Measurable bot metrics and composite scores (deterministic formulas)."""

from __future__ import annotations

from typing import Any, Dict


def default_metric_schema() -> Dict[str, Any]:
    return {
        "tasks_completed": 0,
        "useful_signal_rate": 0.0,
        "false_positive_rate": 0.0,
        "false_negative_rate": 0.0,
        "contribution_to_profitable_trades": 0.0,
        "contribution_to_loss_avoidance": 0.0,
        "avg_latency_ms": 0.0,
        "error_rate": 0.0,
        "token_cost": 0.0,
        "cost_per_useful_output": 0.0,
        "drift_score": 0.0,
        "stability_score": 1.0,
    }


def compute_composite_scores(metrics: Dict[str, Any]) -> Dict[str, float]:
    """utility, efficiency, trust, promotion — explicit weighting (tunable via optimization_objectives)."""
    from trading_ai.global_layer.optimization_objectives import default_weights

    w = default_weights()
    u = float(metrics.get("useful_signal_rate") or 0.0)
    loss_avoid = float(metrics.get("contribution_to_loss_avoidance") or 0.0)
    profit = float(metrics.get("contribution_to_profitable_trades") or 0.0)
    err = float(metrics.get("error_rate") or 0.0)
    tok = float(metrics.get("token_cost") or 0.0)
    stab = float(metrics.get("stability_score") or 0.0)

    utility_score = w.profitability * profit + w.drawdown_reduction * loss_avoid + w.signal_quality * u
    efficiency_score = w.cost_efficiency * (1.0 / (1.0 + tok + metrics.get("cost_per_useful_output", 0.0)))
    trust_score = w.stability * stab + w.explainability * (1.0 - err)
    promotion_score = 0.25 * utility_score + 0.25 * efficiency_score + 0.5 * trust_score

    return {
        "utility_score": round(utility_score, 6),
        "efficiency_score": round(efficiency_score, 6),
        "trust_score": round(trust_score, 6),
        "promotion_score": round(promotion_score, 6),
    }


def merge_metrics_update(existing: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    base = {**default_metric_schema(), **existing}
    base.update(delta)
    base["composite"] = compute_composite_scores(base)
    return base
