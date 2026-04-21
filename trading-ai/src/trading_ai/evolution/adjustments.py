"""Automated adjustments with explicit reasons — confidence, pause, promotion hooks."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from trading_ai.edge.models import EdgeStatus
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.edge.scoring import compute_edge_metrics, metrics_to_dict
from trading_ai.edge.validation import minimum_sample_trades, promote_validated_to_scaled
from trading_ai.evolution.measures import compute_slice_metrics, filter_events
from trading_ai.evolution.scoring import MaturityLevel, maturity_for_edge, unified_edge_score

logger = logging.getLogger(__name__)


def suggest_adjustments(
    events: List[Mapping[str, Any]],
    *,
    registry: Optional[EdgeRegistry] = None,
) -> List[Dict[str, Any]]:
    """Produce machine-readable adjustment proposals (caller may apply selectively)."""
    reg = registry or EdgeRegistry()
    proposals: List[Dict[str, Any]] = []
    for e in reg.list_edges():
        if e.status in (EdgeStatus.REJECTED.value, EdgeStatus.DEPRECATED.value):
            continue
        eid = e.edge_id
        evs = filter_events(events, edge_id=eid)
        m = compute_slice_metrics(eid, evs)
        u, det = unified_edge_score(e, m)
        mat = maturity_for_edge(e.status, m, u)

        if mat == MaturityLevel.DEGRADED and e.status in (
            EdgeStatus.VALIDATED.value,
            EdgeStatus.SCALED.value,
            EdgeStatus.TESTING.value,
        ):
            proposals.append(
                {
                    "edge_id": eid,
                    "action": "consider_pause",
                    "reason": "maturity_degraded_negative_expectancy_or_instability",
                    "evidence": {"expectancy_net": m.expectancy_net, "unified_score": u, **det},
                }
            )

        if e.status == EdgeStatus.VALIDATED.value and m.n >= minimum_sample_trades():
            em = compute_edge_metrics(events, eid)
            if em.post_fee_expectancy > 0 and em.stability_score > 0.4 and m.max_drawdown < abs(m.net_pnl) + 1.0:
                proposals.append(
                    {
                        "edge_id": eid,
                        "action": "consider_scale_promotion",
                        "reason": "positive_post_fee_expectancy_stability_ok_drawdown_bounded",
                        "evidence": metrics_to_dict(em),
                    }
                )

    return proposals


def apply_automated_adjustments(
    events: List[Mapping[str, Any]],
    *,
    registry: Optional[EdgeRegistry] = None,
    apply_pauses: bool = True,
    apply_scaled_promotion: bool = True,
) -> Dict[str, Any]:
    """
    Apply safe automations: pause degraded edges; promote validated→scaled when rules pass.

    Respects profit_reality gates inside validation helpers when enabled.
    """
    reg = registry or EdgeRegistry()
    log_entries: List[Dict[str, Any]] = []
    for prop in suggest_adjustments(events, registry=reg):
        eid = str(prop.get("edge_id") or "")
        act = prop.get("action")
        if act == "consider_pause" and apply_pauses:
            ok = reg.update_status(eid, EdgeStatus.PAUSED.value, reason=str(prop.get("reason")))
            if ok:
                entry = {"edge_id": eid, "change": "status→paused", "why": prop}
                log_entries.append(entry)
                logger.info("evolution adjustment %s", entry)
        if act == "consider_scale_promotion" and apply_scaled_promotion:
            did = promote_validated_to_scaled(reg, eid)
            if did:
                entry = {"edge_id": eid, "change": "validated→scaled", "why": prop}
                log_entries.append(entry)
                logger.info("evolution promotion %s", entry)

    return {"applied": log_entries}
