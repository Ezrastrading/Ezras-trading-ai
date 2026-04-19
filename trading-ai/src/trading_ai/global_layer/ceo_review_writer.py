"""Compact CEO-facing summaries for EOD and exception reviews."""

from __future__ import annotations

from typing import Any, Dict, List


def build_ceo_operational_summary(
    packet: Dict[str, Any],
    joint: Dict[str, Any],
    *,
    review_type: str,
) -> str:
    """
    Brief, operational, evidence-based — no fluff.
    Required sections for eod | exception per execution spec.
    """
    cs = packet.get("capital_state") or {}
    lt = packet.get("live_trading_summary") or {}
    hv = joint.get("house_view") or {}
    lines: List[str] = []
    lines.append(
        f"Capital: equity_usd={cs.get('current_equity_usd')} "
        f"daily_net={cs.get('daily_net_pnl_usd')} weekly_net={cs.get('weekly_net_pnl_usd')}"
    )
    wiw = hv.get("what_is_working") or []
    lines.append(f"Best live edge: {wiw[0] if wiw else packet.get('goal_state', {}).get('current_best_live_edge', '')}")
    lines.append(f"Weakest live edge: {packet.get('goal_state', {}).get('current_weakest_live_edge', '')}")
    lines.append(f"Biggest risk: {(hv.get('top_risk_issues') or [''])[0]}")
    lines.append(f"Best safe improvement: {(hv.get('top_risk_reduction_moves') or [''])[0]}")
    lines.append(f"Best growth opportunity: {(hv.get('top_growth_opportunities') or [''])[0]}")
    lines.append(f"Bottleneck to first million: {joint.get('path_to_first_million_summary', '')}")
    n3 = list(joint.get("changes_recommended") or [])[:3]
    lines.append(f"Immediate next 3: {n3}")
    lines.append(f"Review type: {review_type} live_mode={joint.get('live_mode_recommendation')} conf={joint.get('confidence_score')}")
    return " | ".join(lines)[:4000]


def attach_ceo_summary_to_joint(joint: Dict[str, Any], packet: Dict[str, Any], review_type: str) -> Dict[str, Any]:
    if review_type in ("eod", "exception"):
        joint["ceo_operational_summary"] = build_ceo_operational_summary(packet, joint, review_type=review_type)
    return joint
