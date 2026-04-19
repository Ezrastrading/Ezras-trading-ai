"""Map joint review to safe, bounded actions — never unsafe live auto-deployment."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.governance_formulas import PRODUCTION_DEFAULTS
from trading_ai.global_layer.review_policy import ALLOWED_ACTION_TYPES, FORBIDDEN_ACTION_TYPES, ReviewPolicy
from trading_ai.global_layer.review_storage import ReviewStorage

# Spec 17 — router log action types
ROUTER_ACTION_TYPES = frozenset(
    {
        "caution_flag",
        "queue_update",
        "pause_recommendation",
        "extra_review",
        "governance_note",
        "manual_attention",
    }
)


def _log_action(
    st: ReviewStorage,
    *,
    joint_review_id: str,
    packet_id: str,
    action_type: str,
    target: str,
    reason: str,
    evidence_refs: List[str],
    applied: bool,
    blocked: bool = False,
    block_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if action_type in FORBIDDEN_ACTION_TYPES:
        row = {
            "action_id": f"ra_blocked_{uuid.uuid4().hex[:12]}",
            "joint_review_id": joint_review_id,
            "packet_id": packet_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "action_type": action_type,
            "target": target,
            "reason": reason,
            "evidence_refs": evidence_refs,
            "applied": False,
            "blocked": True,
            "block_reason": "forbidden_action_type",
        }
        st.append_jsonl("review_action_log.jsonl", row)
        return row
    row = {
        "action_id": f"ra_{uuid.uuid4().hex[:12]}",
        "joint_review_id": joint_review_id,
        "packet_id": packet_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "action_type": action_type,
        "target": target,
        "reason": reason,
        "evidence_refs": evidence_refs,
        "applied": applied,
        "blocked": blocked,
        "block_reason": block_reason,
    }
    st.append_jsonl("review_action_log.jsonl", row)
    return row


def route_safe_actions(
    joint: Dict[str, Any],
    *,
    storage: Optional[ReviewStorage] = None,
    policy: Optional[ReviewPolicy] = None,
    packet: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    policy = policy or ReviewPolicy()
    st = storage or ReviewStorage()
    applied: List[Dict[str, Any]] = []
    if not policy.allow_safe_action_router:
        return applied

    jid = str(joint.get("joint_review_id") or "")
    pid = str(joint.get("packet_id") or (packet or {}).get("packet_id") or "unknown")
    live = str(joint.get("live_mode_recommendation") or "caution")
    conf = float(joint.get("confidence_score") or 0.0)
    integ = str(joint.get("review_integrity_state") or "full")

    pause_thr = float(PRODUCTION_DEFAULTS.get("joint_confidence_pause_attention_threshold", 0.40))
    caution_thr = float(PRODUCTION_DEFAULTS.get("joint_confidence_caution_threshold", 0.55))

    # Forbidden auto-actions are never emitted (FORBIDDEN_ACTION_TYPES is a hard deny list for future automation).

    if live == "paused":
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="governance_note",
                target="governance_events.json",
                reason="joint_live_mode_paused",
                evidence_refs=[jid],
                applied=True,
            )
        )
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="pause_recommendation",
                target="live_mode_flags",
                reason="paused_review",
                evidence_refs=[jid],
                applied=True,
            )
        )
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="extra_review",
                target="exception_review",
                reason="pause_followup",
                evidence_refs=[jid],
                applied=True,
            )
        )
        cq = st.load_json("candidate_queue.json")
        items = list(cq.get("items") or [])
        for it in items:
            if isinstance(it, dict) and it.get("status") == "promotion_pending":
                it["status"] = "watch"
        cq["items"] = items[-200:]
        st.save_json("candidate_queue.json", cq)
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="queue_update",
                target="candidate_queue.json",
                reason="block_promotions_temporarily",
                evidence_refs=[jid],
                applied=True,
            )
        )

    elif live == "caution":
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="caution_flag",
                target="avenue_route_flags",
                reason="joint_caution",
                evidence_refs=[jid],
                applied=True,
            )
        )
        rr = st.load_json("risk_reduction_queue.json")
        ri = list(rr.get("items") or [])
        ri.append(
            {
                "id": f"rr_{uuid.uuid4().hex[:10]}",
                "ts": time.time(),
                "source": "joint_review",
                "joint_review_id": jid,
                "priority_boost": True,
            }
        )
        rr["items"] = ri[-300:]
        st.save_json("risk_reduction_queue.json", rr)
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="queue_update",
                target="risk_reduction_queue.json",
                reason="prioritize_risk_reduction",
                evidence_refs=[jid],
                applied=True,
            )
        )
        if conf < caution_thr:
            applied.append(
                _log_action(
                    st,
                    joint_review_id=jid,
                    packet_id=pid,
                    action_type="extra_review",
                    target="closer_cadence",
                    reason="low_joint_confidence",
                    evidence_refs=[jid],
                    applied=True,
                )
            )

    else:
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="governance_note",
                target="monitoring",
                reason="joint_normal_monitoring",
                evidence_refs=[jid],
                applied=True,
            )
        )

    # CEO queue — caution/pause/low confidence / material disagreement already in merger governance_events
    note = {
        "candidate_id": f"ceoq_{uuid.uuid4().hex[:10]}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "class": "governance_candidate",
        "name": "joint_review_note",
        "summary": (joint.get("ceo_summary") or "")[:800],
        "priority": "high" if live in ("paused", "caution") or conf < pause_thr else "medium",
        "status": "watch",
        "review_source": "joint",
        "evidence_strength": "moderate",
        "path_to_goal_relevance": "high",
        "packet_id": pid,
        "joint_review_id": jid,
    }
    cq = st.load_json("ceo_review_queue.json")
    items = list(cq.get("items") or [])
    if live in ("paused", "caution") or conf < pause_thr:
        items.append(note)
    cq["items"] = items[-100:]
    st.save_json("ceo_review_queue.json", cq)
    applied.append(
        _log_action(
            st,
            joint_review_id=jid,
            packet_id=pid,
            action_type="manual_attention",
            target="ceo_review_queue.json",
            reason="ceo_note",
            evidence_refs=[jid],
            applied=live in ("paused", "caution") or conf < pause_thr,
        )
    )

    # Risk-focused triggers from house view
    hv = joint.get("house_view") or {}
    risks = " ".join(hv.get("top_risk_issues") or []).lower()
    if any(k in risks for k in ("slippage", "stale", "verification", "fragility")):
        applied.append(
            _log_action(
                st,
                joint_review_id=jid,
                packet_id=pid,
                action_type="extra_review",
                target="route_tightening_review",
                reason="risk_cluster_in_house_view",
                evidence_refs=[jid],
                applied=True,
            )
        )

    sp = st.load_json("speed_to_goal_review.json")
    sp["summary"] = str(joint.get("path_to_first_million_summary") or "")[:1200]
    sp["accelerators"] = list(joint.get("changes_recommended") or [])[:5]
    sp["integrity"] = integ
    st.save_json("speed_to_goal_review.json", sp)

    gov = st.load_json("governance_events.json")
    ev = list(gov.get("events") or [])
    ev.append(
        {
            "ts": time.time(),
            "kind": "ai_joint_review",
            "joint_review_id": jid,
            "packet_id": pid,
            "live_mode_recommendation": live,
            "confidence": conf,
        }
    )
    gov["events"] = ev[-500:]
    st.save_json("governance_events.json", gov)

    cc = st.load_json("ceo_capital_review.json")
    cc["first_million_path"] = {
        "summary": joint.get("path_to_first_million_summary"),
        "live_mode": live,
        "confidence": conf,
        "integrity": integ,
    }
    cc["recommendations"] = {
        "keep_cut_pause": joint.get("changes_recommended"),
        "blocked": joint.get("changes_blocked"),
    }
    st.save_json("ceo_capital_review.json", cc)

    fmp = st.load_json("first_million_progress_review.json")
    fmp["bottleneck"] = str(joint.get("path_to_first_million_summary") or "")[:500]
    fmp["main_opportunity"] = str((hv.get("top_growth_opportunities") or [""])[0])
    st.save_json("first_million_progress_review.json", fmp)

    return applied


def validate_router_action(action_type: str) -> bool:
    return action_type in ROUTER_ACTION_TYPES


def validate_action(action_type: str) -> bool:
    """Policy gate: forbidden types invalid; allowlisted automation types valid."""
    if action_type in FORBIDDEN_ACTION_TYPES:
        return False
    return action_type in ALLOWED_ACTION_TYPES
