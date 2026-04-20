"""Persistent hierarchy registry + derived relationship / state artifacts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_hierarchy.guards import assert_hierarchy_bot_no_live_authority
from trading_ai.global_layer.bot_hierarchy.models import (
    GateCandidateRecord,
    HierarchyAuthorityLevel,
    HierarchyBotRecord,
    HierarchyBotStatus,
    HierarchyBotType,
    HierarchyLifecycleStage,
    utc_now_iso,
    new_hierarchy_bot,
)
from trading_ai.global_layer.bot_hierarchy.paths import default_bot_hierarchy_root, ensure_hierarchy_dirs

_TRUTH_VERSION = "hierarchy_registry_v1"

EZRA_GOVERNOR_BOT_ID = "ezras_governor"


def _dump_bot(b: HierarchyBotRecord) -> Dict[str, Any]:
    d = b.model_dump(mode="json")
    assert_hierarchy_bot_no_live_authority(d)
    return d


def load_hierarchy_state(path: Optional[Path] = None) -> Dict[str, Any]:
    root = ensure_hierarchy_dirs(path)
    canonical = root / "hierarchy_state.json"
    if not canonical.is_file():
        return {
            "truth_version": _TRUTH_VERSION,
            "updated_at": None,
            "ezra_governor_bot_id": EZRA_GOVERNOR_BOT_ID,
            "bots": [],
            "gate_candidates": [],
        }
    raw = json.loads(canonical.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("hierarchy_state_invalid")
    raw.setdefault("truth_version", _TRUTH_VERSION)
    raw.setdefault("ezra_governor_bot_id", EZRA_GOVERNOR_BOT_ID)
    raw.setdefault("bots", [])
    raw.setdefault("gate_candidates", [])
    return raw


def save_hierarchy_state(state: Dict[str, Any], path: Optional[Path] = None) -> Path:
    root = ensure_hierarchy_dirs(path)
    state = dict(state)
    state["truth_version"] = _TRUTH_VERSION
    state["updated_at"] = utc_now_iso()
    bots = [HierarchyBotRecord.model_validate(b) for b in (state.get("bots") or [])]
    state["bots"] = [_dump_bot(b) for b in bots]
    for c in state.get("gate_candidates") or []:
        GateCandidateRecord.model_validate(c)
    canonical = root / "hierarchy_state.json"
    canonical.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    _materialize_derived_artifacts(root, state)
    return canonical


def _materialize_derived_artifacts(root: Path, state: Dict[str, Any]) -> None:
    bots: List[Dict[str, Any]] = list(state.get("bots") or [])
    (root / "bot_registry.json").write_text(
        json.dumps(
            {"truth_version": _TRUTH_VERSION, "updated_at": state.get("updated_at"), "bots": bots},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    by_avenue: Dict[str, Any] = {}
    by_gate: Dict[str, Any] = {}
    by_worker_parent: Dict[str, Any] = {}
    nodes = [{"id": b["bot_id"], "type": b.get("bot_type")} for b in bots]
    edges: List[Dict[str, str]] = []
    for b in bots:
        bid = str(b.get("bot_id"))
        aid = str(b.get("avenue_id") or "")
        by_avenue.setdefault(aid, {"avenue_id": aid, "avenue_master": None, "gate_managers": []})
        bt = str(b.get("bot_type"))
        if bt == HierarchyBotType.AVENUE_MASTER.value:
            by_avenue[aid]["avenue_master"] = bid
        if bt == HierarchyBotType.GATE_MANAGER.value:
            gid = str(b.get("gate_id") or "")
            gkey = f"{aid}|{gid}"
            by_gate[gkey] = {"avenue_id": aid, "gate_id": gid, "gate_manager": bid, "workers": []}
        if bt == HierarchyBotType.GATE_WORKER.value:
            par = str(b.get("parent_bot_id") or "")
            by_worker_parent.setdefault(par, {"parent_bot_id": par, "workers": []})
            by_worker_parent[par]["workers"].append(bid)
        p = b.get("parent_bot_id")
        if p:
            edges.append({"parent": str(p), "child": bid})
    for gk, gv in by_gate.items():
        ws = [b for b in bots if str(b.get("bot_type")) == HierarchyBotType.GATE_WORKER.value and f"{b.get('avenue_id')}|{b.get('gate_id')}" == gk]
        gv["workers"] = [str(x.get("bot_id")) for x in ws]
    (root / "avenue_master_state.json").write_text(
        json.dumps({"truth_version": _TRUTH_VERSION, "by_avenue": by_avenue}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "gate_manager_state.json").write_text(
        json.dumps({"truth_version": _TRUTH_VERSION, "by_gate": by_gate}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "worker_bot_state.json").write_text(
        json.dumps({"truth_version": _TRUTH_VERSION, "by_parent": by_worker_parent}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "bot_relationship_graph.json").write_text(
        json.dumps({"truth_version": _TRUTH_VERSION, "nodes": nodes, "edges": edges}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gc = state.get("gate_candidates") or []
    (root / "gate_candidates.json").write_text(
        json.dumps({"truth_version": _TRUTH_VERSION, "updated_at": state.get("updated_at"), "items": gc}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ensure_ezra_governor(path: Optional[Path] = None) -> HierarchyBotRecord:
    st = load_hierarchy_state(path)
    bots = [HierarchyBotRecord.model_validate(b) for b in (st.get("bots") or [])]
    if any(b.bot_id == EZRA_GOVERNOR_BOT_ID for b in bots):
        return next(b for b in bots if b.bot_id == EZRA_GOVERNOR_BOT_ID)
    ez = new_hierarchy_bot(
        bot_id=EZRA_GOVERNOR_BOT_ID,
        bot_name="Ezra Governor",
        bot_type=HierarchyBotType.EZRA_GOVERNOR,
        avenue_id="system",
        authority_level=HierarchyAuthorityLevel.GOVERNANCE_ROOT,
        parent_bot_id=None,
        gate_id=None,
        status=HierarchyBotStatus.ACTIVE,
        lifecycle_stage=HierarchyLifecycleStage.OBSERVING,
        knowledge_scope=["global_mission", "risk_constitution", "promotion_policy"],
        reporting_scope=["ceo_review", "orchestration_truth_chain"],
        can_spawn_child_bots=True,
        safety_constraints=["no_automatic_live_permission", "fail_closed_on_ambiguous_truth"],
    )
    bots.append(ez)
    st["bots"] = [b.model_dump(mode="json") for b in bots]
    save_hierarchy_state(st, path=path)
    return ez


def ensure_avenue_master(avenue_id: str, path: Optional[Path] = None) -> HierarchyBotRecord:
    ensure_ezra_governor(path=path)
    st = load_hierarchy_state(path)
    bots = [HierarchyBotRecord.model_validate(b) for b in (st.get("bots") or [])]
    aid = str(avenue_id).strip()
    mid = f"avenue_master_{aid}"
    for b in bots:
        if b.bot_type == HierarchyBotType.AVENUE_MASTER and b.avenue_id == aid:
            return b
    am = new_hierarchy_bot(
        bot_id=mid,
        bot_name=f"Avenue Master {aid}",
        bot_type=HierarchyBotType.AVENUE_MASTER,
        avenue_id=aid,
        authority_level=HierarchyAuthorityLevel.AVENUE_INTELLIGENCE,
        parent_bot_id=EZRA_GOVERNOR_BOT_ID,
        gate_id=None,
        status=HierarchyBotStatus.ACTIVE,
        lifecycle_stage=HierarchyLifecycleStage.OBSERVING,
        knowledge_scope=["venue_mechanics", "strategy_classes", "gate_behavior"],
        reporting_scope=["avenue_master_reports.jsonl", "ceo_daily_orchestration"],
        can_propose_new_gate=True,
        can_spawn_child_bots=True,
    )
    bots.append(am)
    st["bots"] = [b.model_dump(mode="json") for b in bots]
    save_hierarchy_state(st, path=path)
    return am


def upsert_bot(bot: HierarchyBotRecord, path: Optional[Path] = None) -> HierarchyBotRecord:
    st = load_hierarchy_state(path)
    bots = [HierarchyBotRecord.model_validate(b) for b in (st.get("bots") or [])]
    assert_hierarchy_bot_no_live_authority(bot)
    nb = bot.model_copy(update={"updated_at": utc_now_iso()})
    if not nb.created_at:
        nb = nb.model_copy(update={"created_at": utc_now_iso()})
    out: List[HierarchyBotRecord] = []
    replaced = False
    for b in bots:
        if b.bot_id == nb.bot_id:
            out.append(nb)
            replaced = True
        else:
            out.append(b)
    if not replaced:
        out.append(nb)
    st["bots"] = [b.model_dump(mode="json") for b in out]
    save_hierarchy_state(st, path=path)
    return nb


def list_bots(path: Optional[Path] = None) -> List[HierarchyBotRecord]:
    st = load_hierarchy_state(path)
    return [HierarchyBotRecord.model_validate(b) for b in (st.get("bots") or [])]


def children_of(parent_bot_id: str, path: Optional[Path] = None) -> List[HierarchyBotRecord]:
    p = str(parent_bot_id)
    return [b for b in list_bots(path=path) if (b.parent_bot_id or "") == p]
