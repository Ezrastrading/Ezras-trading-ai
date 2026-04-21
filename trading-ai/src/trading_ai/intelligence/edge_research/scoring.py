"""Rank research rows — higher is better; status caps evidence weight."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.intelligence.edge_research.models import ResearchStatus


def status_weight(status: ResearchStatus | str) -> float:
    s = status.value if isinstance(status, ResearchStatus) else str(status)
    order = {
        ResearchStatus.rejected.value: 0.0,
        ResearchStatus.archived.value: 0.05,
        ResearchStatus.hypothesis.value: 0.1,
        ResearchStatus.under_research.value: 0.2,
        ResearchStatus.degraded.value: 0.25,
        ResearchStatus.mock_supported.value: 0.45,
        ResearchStatus.staged_supported.value: 0.7,
        ResearchStatus.live_supported.value: 1.0,
    }
    return order.get(s, 0.15)


def score_record(row: Dict[str, Any]) -> float:
    """Weighted score for ranking — confidence * status_weight."""
    conf = float(row.get("confidence") or 0.0)
    st = row.get("current_status") or ResearchStatus.hypothesis.value
    return conf * status_weight(st)


def rank_records(records: List[Dict[str, Any]], *, reverse: bool = True) -> List[Tuple[float, Dict[str, Any]]]:
    scored = [(score_record(r), r) for r in records]
    scored.sort(key=lambda x: x[0], reverse=reverse)
    return scored


def filter_scoped(records: List[Dict[str, Any]], *, avenue_id: str = "", gate_id: str = "") -> List[Dict[str, Any]]:
    out = []
    for r in records:
        if avenue_id and str(r.get("avenue_id") or "") != avenue_id:
            continue
        if gate_id and str(r.get("gate_id") or "") != gate_id:
            continue
        out.append(r)
    return out
