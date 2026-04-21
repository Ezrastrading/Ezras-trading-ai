"""Safest-bets ranking — not low vol alone: net edge × liquidity × execution reliability."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from trading_ai.edge.registry import EdgeRegistry
from trading_ai.evolution.measures import compute_slice_metrics, filter_events
from trading_ai.evolution.scoring import knowledge_liquidity_score, unified_edge_score
from trading_ai.edge.models import EdgeRecord


def safest_bet_score(record: EdgeRecord, row: Mapping[str, Any], m_slice_dict: Mapping[str, Any]) -> float:
    """Blend unified score with structural safety proxy (low error rate, adequate sample)."""
    u = float(row.get("unified_score") or 0.0)
    fail = float(m_slice_dict.get("failure_or_error_rate") or 0.0)
    deg = float(m_slice_dict.get("degraded_rate") or 0.0)
    n = int(m_slice_dict.get("n") or 0)
    sample_ok = min(1.0, n / 40.0)
    safety = (1.0 - min(0.9, fail + deg)) * (0.5 + 0.5 * sample_ok)
    return max(0.0, min(100.0, u * 0.65 + safety * 100.0 * 0.35))


def rank_safest_edges(
    events: List[Mapping[str, Any]],
    *,
    registry_edges: Optional[List[EdgeRecord]] = None,
) -> List[Dict[str, Any]]:
    reg = registry_edges if registry_edges is not None else EdgeRegistry().list_edges()
    out: List[Dict[str, Any]] = []
    for e in reg:
        if e.status in ("rejected",):
            continue
        evs = filter_events(events, edge_id=e.edge_id)
        ms = compute_slice_metrics(e.edge_id, evs)
        u, det = unified_edge_score(e, ms)
        kl = knowledge_liquidity_score(e, ms)
        row = {"edge_id": e.edge_id, "unified_score": u, "score_detail": det, "knowledge_liquidity_score": kl}
        sb = safest_bet_score(e, row, ms.to_dict())
        out.append(
            {
                **row,
                "safest_score": round(sb, 4),
                "avenue": e.avenue,
                "status": e.status,
                "slice": ms.to_dict(),
            }
        )
    out.sort(key=lambda x: float(x.get("safest_score") or 0.0), reverse=True)
    return out


def safest_venue(events: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Safest *avenue* by aggregate net quality (not investment advice)."""
    venues: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        v = str(ev.get("avenue_name") or "unknown").lower()
        venues.setdefault(v, []).append(dict(ev))
    scored = []
    for v, rows in venues.items():
        m = compute_slice_metrics(v, rows)
        # reliability-weighted expectancy
        qual = m.expectancy_net * (1.0 - m.failure_or_error_rate) * (1.0 - 0.5 * m.degraded_rate)
        scored.append({"avenue": v, "safety_score": qual, "metrics": m.to_dict()})
    scored.sort(key=lambda x: x["safety_score"], reverse=True)
    return {"ranked_venues": scored, "safest_avenue": scored[0]["avenue"] if scored else None}
