"""Rank facts for compact review packets — anomaly-first."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def rank_packet_sections(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce ``review_context_rank`` with highest-priority facts, anomalies, candidates.

    Priority order (spec §14):
    1 hard-stop / write failures
    2 loss clusters
    3 route degradation
    4 best live edge
    5 strongest shadow candidate
    6 risk-reduction opportunity
    7 first-million bottleneck
    8 execution cleanliness
    9 fee/slippage anomalies
    """
    facts: List[str] = []
    anomalies: List[str] = []
    candidates: List[str] = []

    rs = raw.get("risk_summary") or {}
    lt = raw.get("live_trading_summary") or {}
    # Hard-stop signal: packet builder sets live_trading_summary.hard_stop_events; also mirror in risk_summary when present.
    hs = int(rs.get("hard_stop_events") or 0) or int(lt.get("hard_stop_events") or 0)
    if hs > 0:
        anomalies.append("hard_stop_events>0")
    if int(rs.get("write_verification_failures") or 0) > 0:
        anomalies.append("write_verification_failures")
    if int(rs.get("loss_cluster_count") or 0) > 0:
        anomalies.append("loss_cluster")
    if int(rs.get("ws_market_stale_events") or 0) > 0:
        anomalies.append("ws_market_stale")
    if int(rs.get("health_degraded_events") or 0) > 0:
        anomalies.append("health_degraded")

    gs = raw.get("goal_state") or {}
    if gs.get("main_bottleneck_to_first_million"):
        facts.append(f"bottleneck:{gs.get('main_bottleneck_to_first_million')}")
    if gs.get("current_best_live_edge"):
        facts.append(f"best_edge:{gs.get('current_best_live_edge')}")

    sh = raw.get("shadow_exploration_summary") or {}
    if int(sh.get("promotion_pending_count") or 0) > 0:
        candidates.append("promotion_pending")
    top = sh.get("top_profit_candidates") or []
    if isinstance(top, list) and top:
        candidates.append("profit_candidates")

    return {
        "highest_priority_facts": facts[:20],
        "highest_priority_anomalies": anomalies[:20],
        "highest_priority_candidates": candidates[:20],
    }


def trim_packet_for_budget(packet: Dict[str, Any], *, max_chars: int) -> Tuple[Dict[str, Any], bool]:
    """Return packet trimmed if over budget (drop lesson long tails)."""
    import json

    s = json.dumps(packet, default=str)
    if len(s) <= max_chars:
        return packet, False
    p = dict(packet)
    ls = p.get("lesson_state")
    if isinstance(ls, dict):
        for k in ("top_positive_lessons", "top_negative_lessons", "top_immediate_actions"):
            if k in ls and isinstance(ls[k], list):
                ls[k] = ls[k][:3]
    p["lesson_state"] = ls
    s2 = json.dumps(p, default=str)
    if len(s2) > max_chars:
        p["_truncated"] = True
    return p, True
