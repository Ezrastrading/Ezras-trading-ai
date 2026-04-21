"""Unified edge scoring, knowledge×liquidity, and maturity labels — bounded, testable."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from trading_ai.edge.models import EdgeRecord
from trading_ai.edge.scoring import compute_edge_metrics, metrics_to_dict
from trading_ai.evolution.measures import TradeSliceMetrics, compute_slice_metrics, filter_events


class MaturityLevel(str, Enum):
    """Cross-entity maturity — gates, lanes, edges."""

    IMMATURE = "immature"
    EARLY_EVIDENCE = "early_evidence"
    PROMISING = "promising"
    VALIDATED = "validated"
    SCALABLE = "scalable"
    DEGRADED = "degraded"
    PAUSED = "paused"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _recency_weight(sample_n: int, recent_positive_ratio: float = 0.5) -> float:
    """Favor larger samples and recent quality (caller supplies recent ratio 0–1)."""
    sn = _clamp(math.log1p(max(0, sample_n)) / math.log1p(80), 0.0, 1.0)
    return 0.5 * sn + 0.5 * _clamp(recent_positive_ratio, 0.0, 1.0)


def knowledge_score_from_edge(record: EdgeRecord, trade_count: int, measured_expectancy: float) -> float:
    """Higher when registry confidence, history depth, and measured expectancy align."""
    conf = _clamp(float(record.confidence or 0.0))
    depth = _clamp(math.log1p(max(0, trade_count)) / math.log1p(60))
    exp_sig = _clamp(0.5 + 0.5 * math.tanh(measured_expectancy * 25.0))
    return _clamp(0.35 * conf + 0.35 * depth + 0.30 * exp_sig)


def liquidity_quality_proxy(metrics: TradeSliceMetrics, spread_proxy_bps: float = 0.0) -> float:
    """
    0–1 liquidity quality from execution stats (spread proxy optional).

    Penalizes high slippage bps sum, rewards execution quality when present.
    """
    slip_pen = _clamp(1.0 - min(1.0, metrics.n and (metrics.slippage_usd / max(metrics.n, 1)) / 50.0))
    eq_raw = float(getattr(metrics, "avg_exec_quality", 0.0) or getattr(metrics, "avg_execution_quality", 0.0) or 0.0)
    eq = _clamp(eq_raw) if eq_raw > 0 else 0.5
    spread_pen = _clamp(1.0 - min(1.0, spread_proxy_bps / 500.0))
    return _clamp(0.4 * slip_pen + 0.35 * eq + 0.25 * spread_pen)


def knowledge_liquidity_score(
    record: EdgeRecord,
    metrics: TradeSliceMetrics,
    *,
    spread_proxy_bps: float = 0.0,
) -> float:
    k = knowledge_score_from_edge(record, metrics.n, metrics.expectancy_net)
    lq = liquidity_quality_proxy(metrics, spread_proxy_bps=spread_proxy_bps)
    return _clamp(math.sqrt(max(1e-9, k) * max(1e-9, lq)))


def unified_edge_score(
    record: EdgeRecord,
    metrics: TradeSliceMetrics,
    *,
    regime_match: float = 1.0,
    degradation_flag: bool = False,
    recent_win_bias: float = 0.5,
) -> Tuple[float, Dict[str, Any]]:
    """
    Single ranking score 0–100 — higher → more capital, promotion attention.

    Not a guarantee of profit; combines post-fee expectancy, stability, penalties.
    """
    exp = metrics.expectancy_net
    var = metrics.variance_pnl
    std = math.sqrt(var) if var > 0 else 0.0
    stability = exp / (1.0 + std) if std >= 0 else exp
    dd_pen = _clamp(metrics.max_drawdown / max(abs(metrics.net_pnl), 1.0, 1e-6))
    slip_pen = _clamp(metrics.slippage_usd / max(1.0, abs(metrics.net_pnl) + 1.0))
    kl = knowledge_liquidity_score(record, metrics)
    rec_w = _recency_weight(metrics.n, recent_win_bias)
    reg = _clamp(regime_match)

    deg_pen = 0.35 if degradation_flag else 0.0
    eq_raw = float(
        getattr(metrics, "avg_exec_quality", 0.0) or getattr(metrics, "avg_execution_quality", 0.0) or 0.0
    )
    if eq_raw > 0:
        eq_term = 8.0 * _clamp(eq_raw / 100.0)
    else:
        eq_term = 4.0 * kl
    raw = (
        28.0 * math.tanh(exp * 40.0)
        + 18.0 * _clamp(stability / 5.0)
        - 12.0 * dd_pen
        - 10.0 * slip_pen
        + 14.0 * kl
        + eq_term
        + 6.0 * reg
        + 8.0 * rec_w
        - 25.0 * deg_pen
    )
    score = _clamp(50.0 + 18.0 * math.tanh(raw / 18.0), 0.0, 100.0)
    detail = {
        "expectancy_net": exp,
        "stability_proxy": stability,
        "drawdown_penalty": dd_pen,
        "slippage_penalty": slip_pen,
        "knowledge_liquidity": kl,
        "regime_match": reg,
        "recency_weight": rec_w,
        "degradation_penalty": deg_pen,
    }
    return score, detail


def maturity_for_edge(status: str, m: TradeSliceMetrics, unified: float) -> MaturityLevel:
    st = (status or "").lower()
    if st == "paused":
        return MaturityLevel.PAUSED
    if st == "rejected":
        return MaturityLevel.IMMATURE
    n = m.n
    if n < 5:
        return MaturityLevel.IMMATURE
    if n < 15:
        return MaturityLevel.EARLY_EVIDENCE
    if st in ("candidate", "testing") and m.expectancy_net > 0 and unified >= 45:
        return MaturityLevel.PROMISING
    if st == "validated":
        return MaturityLevel.VALIDATED if unified >= 55 else MaturityLevel.DEGRADED
    if st == "scaled":
        return MaturityLevel.SCALABLE if unified >= 60 else MaturityLevel.DEGRADED
    if m.expectancy_net < 0 and n >= 20:
        return MaturityLevel.DEGRADED
    return MaturityLevel.PROMISING


def rank_edges_by_score(
    registry_edges: List[EdgeRecord],
    events: List[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for e in registry_edges:
        eid = e.edge_id
        evs = filter_events(events, edge_id=eid)
        m_slice = compute_slice_metrics(eid, evs)
        em = compute_edge_metrics(events, eid)
        m_dict = metrics_to_dict(em)
        u, det = unified_edge_score(e, m_slice, regime_match=float((e.regime_fit or {}).get("match", 1.0) or 1.0))
        mat = maturity_for_edge(e.status, m_slice, u).value
        rows.append(
            {
                "edge_id": eid,
                "avenue": e.avenue,
                "strategy_lane": e.strategy_lane or e.edge_type,
                "status": e.status,
                "unified_score": round(u, 4),
                "score_detail": det,
                "maturity": mat,
                "knowledge_liquidity_score": round(
                    knowledge_liquidity_score(e, m_slice),
                    4,
                ),
                "metrics": m_dict,
                "slice": m_slice.to_dict(),
            }
        )
    rows.sort(key=lambda r: float(r.get("unified_score") or 0.0), reverse=True)
    return rows
