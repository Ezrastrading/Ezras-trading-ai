"""CEO / review / execution-intelligence integration — advisory snapshots only."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.budget_governor import load_budget_state
from trading_ai.global_layer.bot_hierarchy.knowledge import load_knowledge_snapshot
from trading_ai.global_layer.bot_hierarchy.models import GateCandidateStage, HierarchyBotStatus, HierarchyBotType
from trading_ai.global_layer.bot_hierarchy.paths import default_bot_hierarchy_root
from trading_ai.global_layer.bot_hierarchy.registry import EZRA_GOVERNOR_BOT_ID, children_of, list_bots, load_hierarchy_state
from trading_ai.global_layer.lock_layer.promotion_rung import execution_rung_for_promotion_tier
from trading_ai.global_layer.orchestration_schema import PromotionTier


def _compact_bots(bots: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for b in bots:
        d = b.model_dump(mode="json") if hasattr(b, "model_dump") else dict(b)
        out.append(
            {
                "bot_id": d.get("bot_id"),
                "bot_type": d.get("bot_type"),
                "avenue_id": d.get("avenue_id"),
                "gate_id": d.get("gate_id"),
                "parent_bot_id": d.get("parent_bot_id"),
                "status": d.get("status"),
                "live_permissions": d.get("live_permissions"),
            }
        )
    return out


def build_review_packet_hierarchy_section(*, root: Optional[Path] = None) -> Dict[str, Any]:
    """Compact section for ``review_packet_latest`` — does not allocate capital or permissions."""
    root = root or default_bot_hierarchy_root()
    try:
        st = load_hierarchy_state(root)
        bots = list_bots(path=root)
        masters = [b for b in bots if b.bot_type == HierarchyBotType.AVENUE_MASTER]
        gms = [b for b in bots if b.bot_type == HierarchyBotType.GATE_MANAGER]
        weak = [b.bot_id for b in gms if b.status == HierarchyBotStatus.DEGRADED]
        cands = list(st.get("gate_candidates") or [])
        by_stage: Dict[str, int] = {}
        for c in cands:
            stg = str((c or {}).get("stage") or "unknown")
            by_stage[stg] = by_stage.get(stg, 0) + 1
        blocked = []
        for c in cands:
            br = (c or {}).get("blocked_reasons") or []
            if br:
                blocked.append({"candidate_id": (c or {}).get("candidate_id"), "reasons": br[:5]})
        return {
            "truth_version": "bot_hierarchy_review_section_v1",
            "hierarchy_root": str(root),
            "ezra_governor_bot_id": st.get("ezra_governor_bot_id") or EZRA_GOVERNOR_BOT_ID,
            "avenue_masters": _compact_bots(masters),
            "gate_managers": _compact_bots(gms),
            "weak_gates_hint": weak,
            "gate_candidate_stage_counts": by_stage,
            "gate_candidates_promotion_blockers_sample": blocked[:12],
            "honesty": "Advisory hierarchy snapshot — not execution proof; live authority remains in orchestration + promotion contracts.",
        }
    except Exception as exc:
        return {"truth_version": "bot_hierarchy_review_section_v1", "honesty": f"unavailable:{exc}"}


def build_ceo_hierarchy_attachment(*, registry_path: Optional[Path] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """
    CEO daily review attachment — enumerates masters, candidates, blocked promotions.

    ``registry_path`` is accepted for API symmetry with orchestration; hierarchy uses ``EZRAS_BOT_HIERARCHY_ROOT``.
    """
    _ = registry_path
    bud = load_budget_state()
    glob_cap = int(bud.get("global_daily_token_budget") or 0)
    glob_used = int(bud.get("global_token_used") or 0)
    attach_attempted = True
    attach_outcome = "attached"
    attach_reason = "budget_allows_hierarchy_snapshot"
    if glob_cap and glob_used >= glob_cap:
        attach_outcome = "skipped_by_budget"
        attach_reason = "global_token_budget_exhausted"
    sec = build_review_packet_hierarchy_section(root=root)
    if attach_outcome == "skipped_by_budget":
        sec = {
            "truth_version": "bot_hierarchy_review_section_v1",
            "hierarchy_summary_withheld": True,
            "reason": attach_reason,
        }
    bots = list_bots(path=root or default_bot_hierarchy_root())
    ezra_children = children_of(EZRA_GOVERNOR_BOT_ID, path=root or default_bot_hierarchy_root())
    promising = []
    for c in load_hierarchy_state(root or default_bot_hierarchy_root()).get("gate_candidates") or []:
        stg = str((c or {}).get("stage") or "")
        if stg in (
            GateCandidateStage.SIM_PASSED.value,
            GateCandidateStage.STAGED_RUNTIME_CANDIDATE.value,
            GateCandidateStage.SUPERVISED_LIVE_CANDIDATE.value,
        ):
            promising.append(
                {
                    "candidate_id": (c or {}).get("candidate_id"),
                    "avenue_id": (c or {}).get("avenue_id"),
                    "gate_id": (c or {}).get("gate_id"),
                    "stage": stg,
                }
            )
    today = []
    tomorrow = []
    for b in bots:
        if b.bot_type == HierarchyBotType.GATE_MANAGER and b.status == HierarchyBotStatus.ACTIVE:
            today.append(f"collect_evidence:{b.bot_id}")
    tomorrow = [f"replay_review:{b.bot_id}" for b in bots if b.bot_type == HierarchyBotType.AVENUE_MASTER][:5]
    return {
        "truth_version": "bot_hierarchy_ceo_attachment_v1",
        "token_budget_source": "global_layer.budget_governor.load_budget_state",
        "token_budget_limit": glob_cap,
        "token_budget_used": glob_used,
        "hierarchy_attach_attempted": attach_attempted,
        "hierarchy_attach_outcome": attach_outcome,
        "hierarchy_attach_reason": attach_reason,
        "registry_path_note": str(registry_path) if registry_path else "orchestration_registry_independent",
        "hierarchy_summary": sec,
        "ezra_child_avenue_masters": [b.bot_id for b in ezra_children],
        "promising_gate_candidates": promising[:20],
        "best_next_steps_today": today[:20],
        "best_next_steps_tomorrow": tomorrow[:20],
        "orchestration_ladder_reference": {
            "promotion_tiers": [t.value for t in PromotionTier],
            "execution_rung_for_T2": execution_rung_for_promotion_tier(PromotionTier.T2.value).value,
            "note": "Gate-candidate stages are parallel documentation — runtime promotion still uses orchestration tiers + proof artifacts.",
        },
        "honesty": "Does not grant live permissions; aligns with ExecutionRung / PromotionTier semantics in-repo.",
    }


def build_execution_intelligence_hierarchy_advisory(*, root: Optional[Path] = None) -> Dict[str, Any]:
    """Advisory context only — never treated as runtime proof."""
    snap = load_knowledge_snapshot(root=root)
    sec = build_review_packet_hierarchy_section(root=root)
    return {
        "truth_version": "ei_hierarchy_advisory_v1",
        "advisory_only": True,
        "is_runtime_proof": False,
        "manager_summaries": sec,
        "knowledge_indexes_present": list(snap.keys()),
        "honesty": "Manager summaries and knowledge indexes are not substitute for replay/sim/staged truth artifacts.",
    }


def hierarchy_health_report(*, root: Optional[Path] = None) -> Dict[str, Any]:
    root = root or default_bot_hierarchy_root()
    st = load_hierarchy_state(root)
    bots = list_bots(path=root)
    issues: List[str] = []
    if not any(b.bot_id == EZRA_GOVERNOR_BOT_ID for b in bots):
        issues.append("missing_ezra_governor_record")
    avenues = {str(b.avenue_id) for b in bots if b.bot_type == HierarchyBotType.AVENUE_MASTER}
    for a in ("A", "B", "C"):
        if a not in avenues:
            issues.append(f"no_avenue_master_for_{a}")
    degraded = [b.bot_id for b in bots if b.status == HierarchyBotStatus.DEGRADED]
    return {
        "truth_version": "bot_hierarchy_health_v1",
        "hierarchy_root": str(root),
        "bot_count": len(bots),
        "candidate_count": len(st.get("gate_candidates") or []),
        "issues": issues,
        "degraded_bots": degraded,
        "next_steps": ["ensure_ezra_governor", "ensure_avenue_master_per_avenue", "advance_candidates_with_evidence_only"],
    }
