"""Aggregate reality verdict per edge + venue (measurement only)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.reality.edge_truth import EdgeTruthEngine
from trading_ai.reality.sample_validation import validate_sample


def net_expectancy_for_edge(edge_summary: Dict[str, Any]) -> float:
    """Prefer 100 / 50 / 20 window net_expectancy (first available, largest n)."""
    windows = edge_summary.get("windows") or {}
    for w in ("100", "50", "20"):
        block = windows.get(w)
        if isinstance(block, dict) and "net_expectancy" in block:
            return float(block.get("net_expectancy") or 0.0)
    return 0.0


def execution_quality_label(execution_flag: str) -> str:
    return "POOR" if execution_flag == "EXECUTION_KILLING_EDGE" else "GOOD"


def build_reality_verdict(
    *,
    edge_id: str,
    venue: str,
    edge_summary: Dict[str, Any],
    net_pnls_for_edge: list,
    execution_flag: str,
    discipline_score: int,
    sample_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Global row for one edge + venue:

    verdict REAL when post-fee expectancy is positive, execution is not fee-dominated,
    discipline holds, and the sample is not LOW-confidence noise.
    """
    _ = venue
    net_exp = net_expectancy_for_edge(edge_summary)
    if sample_result is None:
        sample_result = validate_sample(net_pnls_for_edge).to_dict()
    conf = str(sample_result.get("confidence_level") or "LOW")
    exec_q = execution_quality_label(execution_flag)
    real = (
        net_exp > 0
        and execution_flag != "EXECUTION_KILLING_EDGE"
        and discipline_score >= 80
        and conf in ("MEDIUM", "HIGH", "STRONG")
    )
    return {
        "edge_id": edge_id,
        "venue": venue,
        "net_expectancy": net_exp,
        "execution_quality": exec_q,
        "discipline_score": discipline_score,
        "confidence_level": conf,
        "verdict": "REAL" if real else "NOISE",
    }


def verdict_from_engines(
    *,
    edge_id: str,
    venue: str,
    edge_engine: EdgeTruthEngine,
    net_pnls: list,
    execution_flag: str,
    discipline_score: int,
) -> Dict[str, Any]:
    summary = edge_engine.summary_for_edge(edge_id)
    sr = validate_sample(net_pnls).to_dict()
    return build_reality_verdict(
        edge_id=edge_id,
        venue=venue,
        edge_summary=summary,
        net_pnls_for_edge=net_pnls,
        execution_flag=execution_flag,
        discipline_score=discipline_score,
        sample_result=sr,
    )
