"""New gate discovery — research-only entry; stage advances one rung at a time."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from trading_ai.global_layer.bot_hierarchy.guards import assert_hierarchy_bot_no_live_authority, assert_no_stage_skip
from trading_ai.global_layer.bot_hierarchy.models import (
    GateCandidateRecord,
    GateCandidateStage,
    HierarchyAuthorityLevel,
    HierarchyBotStatus,
    HierarchyBotType,
    HierarchyLifecycleStage,
    GATE_CANDIDATE_STAGE_ORDER,
    utc_now_iso,
    new_hierarchy_bot,
)
from trading_ai.global_layer.bot_hierarchy.registry import (
    ensure_avenue_master,
    load_hierarchy_state,
    save_hierarchy_state,
    upsert_bot,
)

_SLUG = re.compile(r"[^a-z0-9_]+")


def _slug(s: str) -> str:
    t = _SLUG.sub("_", str(s).strip().lower()).strip("_")
    return t or "gate"


def discover_gate_candidate(
    *,
    avenue_id: str,
    gate_id: str,
    strategy_thesis: str,
    edge_hypothesis: str,
    execution_path: str,
    expected_conditions: Optional[Sequence[str]] = None,
    expected_pnl_shape_notes: str = "",
    limits: Optional[Dict[str, Any]] = None,
    constraints: Optional[Sequence[str]] = None,
    kill_conditions: Optional[Sequence[str]] = None,
    required_proofs: Optional[Sequence[str]] = None,
    recommended_worker_roles: Optional[Sequence[str]] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Create a gate candidate in ``discovered`` plus hierarchy bots (gate manager + optional workers).
    Does not touch orchestration live registry or execution authority.
    """
    st = load_hierarchy_state(path)
    av = _slug(avenue_id)
    g = _slug(gate_id)
    cand_id = f"gc_{av}_{g}_{utc_now_iso().replace(':', '').replace('-', '')[:14]}"
    gm_id = f"gate_mgr_{av}_{g}"
    workers_default = list(recommended_worker_roles or ("scanner", "validation", "replay"))
    cand = GateCandidateRecord(
        candidate_id=cand_id,
        avenue_id=str(avenue_id).strip(),
        gate_id=str(gate_id).strip(),
        strategy_thesis=strategy_thesis,
        edge_hypothesis=edge_hypothesis,
        execution_path=execution_path,
        expected_conditions=list(expected_conditions or ()),
        expected_pnl_shape_notes=expected_pnl_shape_notes or "Not a performance guarantee; descriptive hypothesis only.",
        limits=dict(limits or {}),
        constraints=list(constraints or ()),
        kill_conditions=list(kill_conditions or ()),
        required_proofs=list(required_proofs or ("replay_artifact", "sim_scorecard", "staged_runtime_truth")),
        stage=GateCandidateStage.DISCOVERED,
        gate_manager_bot_id=gm_id,
        recommended_worker_roles=list(workers_default),
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    am = ensure_avenue_master(avenue_id, path=path)
    gm = new_hierarchy_bot(
        bot_id=gm_id,
        bot_name=f"Gate Manager {avenue_id}/{gate_id}",
        bot_type=HierarchyBotType.GATE_MANAGER,
        avenue_id=str(avenue_id).strip(),
        authority_level=HierarchyAuthorityLevel.GATE_INTELLIGENCE,
        parent_bot_id=am.bot_id,
        gate_id=str(gate_id).strip(),
        status=HierarchyBotStatus.ACTIVE,
        lifecycle_stage=HierarchyLifecycleStage.OBSERVING,
        knowledge_scope=["entry_logic", "exits", "failure_modes", "calibration"],
        execution_scope=["advisory_only"],
        reporting_scope=["gate_manager_reports.jsonl"],
        can_spawn_child_bots=True,
        current_objectives=["document_gate_hypothesis", "collect_evidence_for_promotion_ladder"],
    )
    assert_hierarchy_bot_no_live_authority(gm)
    upsert_bot(gm, path=path)
    workers_created: List[str] = []
    for i, role in enumerate(workers_default):
        wid = f"worker_{av}_{g}_{_slug(role)}_{i}"
        w = new_hierarchy_bot(
            bot_id=wid,
            bot_name=f"Worker {role}",
            bot_type=HierarchyBotType.GATE_WORKER,
            avenue_id=str(avenue_id).strip(),
            authority_level=HierarchyAuthorityLevel.WORKER_NARROW,
            parent_bot_id=gm_id,
            gate_id=str(gate_id).strip(),
            status=HierarchyBotStatus.PLANNED,
            lifecycle_stage=HierarchyLifecycleStage.INIT,
            knowledge_scope=[str(role)],
            execution_scope=["narrow_scoped_task"],
            reporting_scope=["worker_reports.jsonl"],
            can_spawn_child_bots=False,
            current_objectives=[f"run_{role}_task"],
        )
        assert_hierarchy_bot_no_live_authority(w)
        upsert_bot(w, path=path)
        workers_created.append(wid)
    st = load_hierarchy_state(path)
    items = list(st.get("gate_candidates") or [])
    items.append(cand.model_dump(mode="json"))
    st["gate_candidates"] = items
    save_hierarchy_state(st, path=path)
    return {
        "ok": True,
        "candidate_id": cand_id,
        "gate_manager_bot_id": gm_id,
        "worker_bot_ids": workers_created,
        "stage": GateCandidateStage.DISCOVERED.value,
        "honesty": "Research object only — promotion requires existing proof ladder + orchestration contracts.",
    }


def advance_gate_candidate_stage(
    candidate_id: str,
    *,
    to_stage: str,
    evidence_refs: Optional[Sequence[str]] = None,
    blocked_reasons: Optional[Sequence[str]] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Advance exactly one rung forward (or no-op if same stage)."""
    st = load_hierarchy_state(path)
    items = list(st.get("gate_candidates") or [])
    found = None
    idx = -1
    for i, it in enumerate(items):
        if str((it or {}).get("candidate_id")) == str(candidate_id):
            found = dict(it)
            idx = i
            break
    if not found:
        raise ValueError(f"unknown_gate_candidate:{candidate_id}")
    cur = str(found.get("stage") or "")
    nxt = str(to_stage).strip()
    if cur not in GATE_CANDIDATE_STAGE_ORDER or nxt not in GATE_CANDIDATE_STAGE_ORDER:
        raise ValueError("invalid_stage_value")
    if cur == nxt:
        return {"ok": True, "stage": cur, "note": "no_change"}
    assert_no_stage_skip(cur, nxt)
    found["stage"] = nxt
    found["updated_at"] = utc_now_iso()
    er = list(found.get("evidence_refs") or [])
    er.extend([str(x) for x in (evidence_refs or ()) if str(x).strip()])
    found["evidence_refs"] = er
    br = list(found.get("blocked_reasons") or [])
    br.extend([str(x) for x in (blocked_reasons or ()) if str(x).strip()])
    found["blocked_reasons"] = br
    rec = GateCandidateRecord.model_validate(found)
    items[idx] = rec.model_dump(mode="json")
    st["gate_candidates"] = items
    save_hierarchy_state(st, path=path)
    return {"ok": True, "candidate_id": candidate_id, "stage": nxt}


def build_gate_candidate_from_review_stub(
    *,
    avenue_id: str,
    gate_id: str,
    review_excerpt: str,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Deterministic helper when a human/CEO review text proposes a gate — still research-only."""
    return discover_gate_candidate(
        avenue_id=avenue_id,
        gate_id=gate_id,
        strategy_thesis=f"Derived from review stub — {review_excerpt[:240]}",
        edge_hypothesis="To be validated via replay/sim; not assumed true.",
        execution_path="unspecified_pending_design",
        expected_conditions=[],
        path=path,
    )
