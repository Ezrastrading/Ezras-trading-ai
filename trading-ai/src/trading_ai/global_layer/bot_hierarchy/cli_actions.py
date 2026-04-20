"""CLI handlers for ``python -m trading_ai.deployment`` bot-hierarchy commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.bot_hierarchy.gate_discovery import advance_gate_candidate_stage, build_gate_candidate_from_review_stub, discover_gate_candidate
from trading_ai.global_layer.bot_hierarchy.integration import (
    build_execution_intelligence_hierarchy_advisory,
    build_review_packet_hierarchy_section,
    hierarchy_health_report,
)
from trading_ai.global_layer.bot_hierarchy.registry import ensure_avenue_master, list_bots, load_hierarchy_state
from trading_ai.global_layer.bot_hierarchy.models import HierarchyBotType
from trading_ai.global_layer.bot_hierarchy.reporting import append_gate_research_report as append_gate_research_row


def cmd_list_bot_hierarchy(*, root: Optional[Path] = None) -> Dict[str, Any]:
    st = load_hierarchy_state(root)
    return {
        "truth_version": st.get("truth_version"),
        "updated_at": st.get("updated_at"),
        "bots": [b.model_dump(mode="json") for b in list_bots(path=root)],
        "gate_candidates": st.get("gate_candidates") or [],
    }


def cmd_avenue_master_status(*, avenue: str, root: Optional[Path] = None) -> Dict[str, Any]:
    ensure_avenue_master(avenue, path=root)
    bots = list_bots(path=root)
    am = next((b for b in bots if b.bot_type == HierarchyBotType.AVENUE_MASTER and b.avenue_id == avenue), None)
    children = [b for b in bots if am and b.parent_bot_id == am.bot_id]
    return {
        "avenue_id": avenue,
        "avenue_master": am.model_dump(mode="json") if am else None,
        "children": [c.model_dump(mode="json") for c in children],
    }


def cmd_gate_manager_status(*, avenue: str, gate: str, root: Optional[Path] = None) -> Dict[str, Any]:
    bots = list_bots(path=root)
    gm = next(
        (
            b
            for b in bots
            if b.bot_type == HierarchyBotType.GATE_MANAGER and b.avenue_id == avenue and str(b.gate_id) == str(gate)
        ),
        None,
    )
    workers = [b for b in bots if gm and b.parent_bot_id == gm.bot_id]
    return {
        "avenue_id": avenue,
        "gate_id": gate,
        "gate_manager": gm.model_dump(mode="json") if gm else None,
        "workers": [w.model_dump(mode="json") for w in workers],
    }


def cmd_discover_gate_candidate(
    *,
    avenue: str,
    gate: str,
    thesis: str,
    edge: str,
    exec_path: str,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    return discover_gate_candidate(
        avenue_id=avenue,
        gate_id=gate,
        strategy_thesis=thesis,
        edge_hypothesis=edge,
        execution_path=exec_path,
        path=root,
    )


def cmd_build_gate_candidate_from_review(*, avenue: str, gate: str, excerpt: str, root: Optional[Path] = None) -> Dict[str, Any]:
    return build_gate_candidate_from_review_stub(avenue_id=avenue, gate_id=gate, review_excerpt=excerpt, path=root)


def cmd_promote_gate_candidate_report(*, candidate_id: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """Truth: current stage, blockers, next stage — does not perform orchestration promotion."""
    st = load_hierarchy_state(root)
    found = None
    for c in st.get("gate_candidates") or []:
        if str((c or {}).get("candidate_id")) == str(candidate_id):
            found = dict(c)
            break
    if not found:
        return {"ok": False, "error": "candidate_not_found", "candidate_id": candidate_id}
    from trading_ai.global_layer.bot_hierarchy.models import GATE_CANDIDATE_STAGE_ORDER

    cur = str(found.get("stage") or "")
    try:
        i = GATE_CANDIDATE_STAGE_ORDER.index(cur)
        nxt = GATE_CANDIDATE_STAGE_ORDER[i + 1] if i + 1 < len(GATE_CANDIDATE_STAGE_ORDER) else None
    except ValueError:
        nxt = None
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "current_stage": cur,
        "next_stage": nxt,
        "blocked_reasons": found.get("blocked_reasons") or [],
        "evidence_refs": found.get("evidence_refs") or [],
        "honesty": "Advance stages via gate-candidate-advance with evidence — not via this report alone.",
    }


def cmd_gate_candidate_advance(*, candidate_id: str, to_stage: str, root: Optional[Path] = None) -> Dict[str, Any]:
    return advance_gate_candidate_stage(candidate_id, to_stage=to_stage, path=root)


def cmd_execution_intelligence_bot_report(*, root: Optional[Path] = None) -> Dict[str, Any]:
    return build_execution_intelligence_hierarchy_advisory(root=root)


def cmd_bot_hierarchy_health_report(*, root: Optional[Path] = None) -> Dict[str, Any]:
    return hierarchy_health_report(root=root)


def cmd_append_gate_research_report(payload_json: str, *, root: Optional[Path] = None) -> Dict[str, Any]:
    row = json.loads(payload_json)
    p = append_gate_research_row(row, root=root)
    return {"ok": True, "path": str(p)}
