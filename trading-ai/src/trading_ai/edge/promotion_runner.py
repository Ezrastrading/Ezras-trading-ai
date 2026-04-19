"""Batch evaluation: score edges and apply validation rules."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Sequence

from trading_ai.edge.feedback import feedback_from_evaluation
from trading_ai.edge.models import EdgeStatus
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.edge.validation import apply_evaluation

logger = logging.getLogger(__name__)


def run_promotion_cycle(
    events: Sequence[Mapping[str, Any]],
    *,
    registry: EdgeRegistry | None = None,
) -> Dict[str, Any]:
    """Evaluate every non-terminal edge that has registry presence."""
    reg = registry or EdgeRegistry()
    reports: Dict[str, Any] = {}
    changed: List[str] = []
    for e in reg.list_edges():
        if e.status in (EdgeStatus.DEPRECATED.value,):
            continue
        did_change, rep = apply_evaluation(reg, list(events), e.edge_id)
        reports[e.edge_id] = rep
        if did_change:
            changed.append(e.edge_id)
            feedback_from_evaluation(e.edge_id, rep)
    return {"reports": reports, "updated_edge_ids": changed}
