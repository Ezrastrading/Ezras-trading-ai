"""Merge Claude + GPT into deterministic house view — conservative on disagreement."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from trading_ai.global_layer.review_confidence import (
    compute_agreement_score,
    compute_anomaly_aggregate_score,
    compute_joint_confidence,
    compute_packet_completeness_score,
    sample_strength_from_packet,
)
from trading_ai.global_layer.review_integrity import classify_integrity
from trading_ai.global_layer.review_schema import LIVE_MODES
from trading_ai.global_layer.review_storage import ReviewStorage


def _usable(out: Dict[str, Any]) -> bool:
    return bool(out.get("_validation_ok", True)) and not (out.get("error") == "validation_failed")


def _packet_anomaly_strings(packet: Dict[str, Any]) -> List[str]:
    rs = packet.get("risk_summary") or {}
    lt = packet.get("live_trading_summary") or {}
    out: List[str] = []
    if int(rs.get("write_verification_failures") or 0) > 0:
        out.append("verification_write_failure_signal")
    if int(rs.get("loss_cluster_count") or 0) > 0:
        out.append("loss_cluster_signal")
    if int(rs.get("ws_market_stale_events") or 0) > 0:
        out.append("market_ws_stale_signal")
    if int(rs.get("ws_user_stale_events") or 0) > 0:
        out.append("user_ws_stale_signal")
    if int(rs.get("slippage_cluster_events") or 0) > 0:
        out.append("slippage_cluster_signal")
    if int(lt.get("hard_stop_events") or 0) > 0:
        out.append("hard_stop_signal")
    return out


def _serious_packet_risk(packet: Dict[str, Any]) -> bool:
    rs = packet.get("risk_summary") or {}
    lt = packet.get("live_trading_summary") or {}
    if int(rs.get("write_verification_failures") or 0) > 0:
        return True
    if int(lt.get("hard_stop_events") or 0) > 0:
        return True
    an = _packet_anomaly_strings(packet)
    return len(an) >= 2


def _non_trivial_packet_risk(packet: Dict[str, Any]) -> bool:
    rs = packet.get("risk_summary") or {}
    if int(rs.get("loss_cluster_count") or 0) > 0:
        return True
    if int(rs.get("write_verification_failures") or 0) > 0:
        return True
    return bool(_packet_anomaly_strings(packet))


def _major_anomaly(packet: Dict[str, Any]) -> bool:
    rs = packet.get("risk_summary") or {}
    return int(rs.get("loss_cluster_count") or 0) > 0 or int(rs.get("write_verification_failures") or 0) > 0


def _merge_live_mode(
    cl: str,
    gp: str,
    packet: Dict[str, Any],
    *,
    cl_usable: bool,
    gp_usable: bool,
) -> str:
    """Conservative precedence: paused > caution > normal (spec 7.4)."""
    modes = {"normal": 0, "caution": 1, "paused": 2}
    inv = {0: "normal", 1: "caution", 2: "paused"}

    def eff(which: str, usable: bool) -> Optional[str]:
        if not usable:
            return None
        return which if which in LIVE_MODES else "caution"

    ecl, egp = eff(cl, cl_usable), eff(gp, gp_usable)
    serious = _serious_packet_risk(packet)
    non_triv = _non_trivial_packet_risk(packet)
    major = _major_anomaly(packet)

    if (ecl == "paused" or egp == "paused") and (serious or major):
        return "paused"
    if (ecl == "paused" or egp == "paused"):
        return "paused"
    if (ecl == "caution" or egp == "caution") and non_triv:
        return "caution"
    if ecl == "normal" and egp == "normal" and not major:
        return "normal"
    scores = [modes[m] for m in (ecl, egp) if m is not None]
    if not scores:
        return "caution"
    return inv[max(scores)]


def _dedupe_keep_order(items: List[str], *, limit: int) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        s = (x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _blocked_recommendations(joint_live: str) -> List[str]:
    base = [
        "deploy unreviewed live strategy",
        "disable hard stops",
        "scale size aggressively beyond policy",
        "clear verification failures without root cause",
        "override governance constraints",
    ]
    if joint_live in ("paused", "caution"):
        base.append("promote shadow to live without review")
    return base


def merge_reviews(
    packet: Dict[str, Any],
    claude: Dict[str, Any],
    gpt: Dict[str, Any],
    *,
    storage: Optional[ReviewStorage] = None,
) -> Dict[str, Any]:
    st = storage or ReviewStorage()
    pid = str(packet.get("packet_id") or "unknown")
    jid = f"jr_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    cl_usable = _usable(claude)
    gp_usable = _usable(gpt)
    packet_valid = bool(pid and pid != "unknown")

    integrity = classify_integrity(packet_valid, cl_usable, gp_usable)

    cl_rm = str(claude.get("risk_mode_recommendation") or "caution")
    gp_ls = str(gpt.get("live_status_recommendation") or "caution")
    live_mode = _merge_live_mode(cl_rm, gp_ls, packet, cl_usable=cl_usable, gp_usable=gp_usable)

    p_anom = _packet_anomaly_strings(packet)
    top_risk: List[str] = []
    top_risk.extend(p_anom[:3])
    if cl_usable:
        top_risk.append(str(claude.get("biggest_risk_now") or ""))
        top_risk.append(str(claude.get("most_fragile_part_of_system") or ""))
    if gp_usable:
        top_risk.extend(list(gpt.get("top_3_warnings") or [])[:3])
    top_risk = _dedupe_keep_order(top_risk, limit=12)

    what_work: List[str] = []
    if cl_usable:
        what_work.extend(list(claude.get("what_is_working") or [])[:6])
    if gp_usable:
        what_work.append(str(gpt.get("best_live_edge_now") or ""))
    av = packet.get("avenue_state") or {}
    for row in (av.get("avenue_summary") or [])[:3]:
        if isinstance(row, dict) and row.get("name"):
            what_work.append(f"avenue:{row.get('name')} net={row.get('net_pnl_usd')}")
    what_work = _dedupe_keep_order(what_work, limit=12)

    what_fail: List[str] = []
    if cl_usable:
        what_fail.extend(list(claude.get("what_is_not_working") or [])[:6])
    if gp_usable:
        what_fail.extend(list(gpt.get("top_3_warnings") or [])[:4])
    what_fail.extend(p_anom)
    what_fail = _dedupe_keep_order(what_fail, limit=14)

    growth: List[str] = []
    if gp_usable:
        growth.append(str(gpt.get("best_growth_opportunity") or ""))
    sh = packet.get("shadow_exploration_summary") or {}
    if sh.get("top_profit_candidates"):
        growth.append("shadow_candidates_present")
    if cl_usable:
        growth.append(str(claude.get("best_safe_improvement") or ""))
    growth = _dedupe_keep_order(growth, limit=8)

    watch: List[str] = []
    if cl_usable:
        watch.append(str(claude.get("best_shadow_candidate_to_watch") or ""))
    for x in (sh.get("top_profit_candidates") or [])[:2]:
        if isinstance(x, dict) and x.get("name"):
            watch.append(str(x.get("name")))
    watch = _dedupe_keep_order(watch, limit=8)

    risk_moves: List[str] = []
    if cl_usable:
        risk_moves.append(str(claude.get("best_safe_improvement") or ""))
        risk_moves.append(str(claude.get("worst_live_behavior_to_cut") or ""))
    if gp_usable:
        for a in list(gpt.get("top_3_next_actions") or [])[:3]:
            al = str(a).lower()
            if any(k in al for k in ("risk", "slip", "stop", "tight", "verify", "pause", "reduce")):
                risk_moves.append(str(a))
    risk_moves = _dedupe_keep_order(risk_moves, limit=10)

    changes_rec: List[str] = []
    if gp_usable:
        changes_rec.extend(list(gpt.get("top_3_next_actions") or [])[:6])
    if live_mode == "caution":
        changes_rec.append("hold live mode at caution")
    if live_mode == "paused":
        changes_rec.append("pause new route promotions")

    agreement = 50.0
    if cl_usable and gp_usable:
        agreement = compute_agreement_score(claude, gpt)

    live_mode_disagreement = (
        cl_usable
        and gp_usable
        and str(claude.get("risk_mode_recommendation") or "") != str(gpt.get("live_status_recommendation") or "")
    )

    pkt_comp = compute_packet_completeness_score(packet)
    rs = packet.get("risk_summary") or {}
    max_sev = float(rs.get("max_anomaly_severity") or (70 if int(rs.get("write_verification_failures") or 0) > 0 else 0))
    anom_agg = compute_anomaly_aggregate_score(packet, max_severity=max_sev if max_sev else None)
    sample_s = sample_strength_from_packet(packet)

    cc = float(claude.get("confidence_score") or 0.0) if cl_usable else 0.0
    gc = float(gpt.get("confidence_score") or 0.0) if gp_usable else 0.0
    if cl_usable and not gp_usable:
        gc = cc
    if gp_usable and not cl_usable:
        cc = gc

    conf = compute_joint_confidence(
        claude_confidence_01=cc,
        gpt_confidence_01=gc,
        packet_completeness_0_100=pkt_comp,
        agreement_score_0_100=agreement,
        anomaly_aggregate_0_100=anom_agg,
        sample_strength_0_100=sample_s,
        review_integrity=integrity,
        live_mode_disagreement=live_mode_disagreement,
        anomaly_aggregate_for_cap=anom_agg,
    )

    ceo_summary = str(gpt.get("short_ceo_note") or "") if gp_usable else ""
    path_sum = ""
    if cl_usable:
        path_sum = str(claude.get("path_to_first_million_note") or "")
    if gp_usable:
        path_sum = path_sum or str(gpt.get("main_bottleneck_to_first_million") or "")

    gov_note: Dict[str, Any] = {}
    if live_mode_disagreement:
        gov_note = {
            "kind": "model_disagreement",
            "claude_risk_mode": cl_rm,
            "gpt_live_status": gp_ls,
            "resolution": live_mode,
        }

    out: Dict[str, Any] = {
        "joint_review_id": jid,
        "packet_id": pid,
        "claude_review_id": (str(claude.get("review_id")) if cl_usable else None),
        "gpt_review_id": (str(gpt.get("review_id")) if gp_usable else None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_integrity_state": integrity.value,
        "house_view": {
            "what_is_working": what_work,
            "what_is_failing": what_fail,
            "top_risk_issues": top_risk,
            "top_growth_opportunities": growth,
            "top_candidates_to_watch": watch,
            "top_risk_reduction_moves": risk_moves,
        },
        "live_mode_recommendation": live_mode,
        "changes_recommended": changes_rec[:20],
        "changes_blocked": _blocked_recommendations(live_mode),
        "promotion_watchlist": watch[:10],
        "ceo_summary": ceo_summary[:2000],
        "path_to_first_million_summary": path_sum[:2000],
        "confidence_score": round(conf, 4),
        "_governance_notes": gov_note,
    }

    if gov_note:
        gov = st.load_json("governance_events.json")
        ev = list(gov.get("events") or [])
        ev.append({"ts": time.time(), "kind": "review_disagreement", **gov_note})
        gov["events"] = ev[-500:]
        st.save_json("governance_events.json", gov)

    st.save_json("joint_review_latest.json", {k: v for k, v in out.items() if not k.startswith("_")})
    st.append_jsonl("joint_review_history.jsonl", {"ts": time.time(), "joint_review_id": jid, "packet_id": pid})
    snap = st.load_json("review_scheduler_state.json")
    snap["last_joint_review_id"] = jid
    snap["last_packet_id"] = pid
    st.save_json("review_scheduler_state.json", snap)
    return out
