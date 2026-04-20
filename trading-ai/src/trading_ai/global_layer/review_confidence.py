"""Joint confidence, packet completeness, agreement — governance formulas spec."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.governance_formulas import clamp01, clamp100, sample_strength_from_trade_count
from trading_ai.global_layer.review_integrity import ReviewIntegrityState


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _similar(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na in nb or nb in na or (len(na) > 12 and len(nb) > 12 and na[:20] == nb[:20])


def component_agreement(a: str, b: str) -> float:
    """1.0 exact/near, 0.5 partial, 0.0 contradiction."""
    if _similar(a, b):
        return 1.0
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.5
    wa, wb = set(na.split()), set(nb.split())
    if wa & wb:
        return 0.5
    return 0.0


def compute_agreement_score(
    claude: Dict[str, Any],
    gpt: Dict[str, Any],
) -> float:
    """Model agreement score (0–100)."""
    cl_rm = str(claude.get("risk_mode_recommendation") or "")
    gp_ls = str(gpt.get("live_status_recommendation") or "")
    c1 = component_agreement(cl_rm, gp_ls)

    c2 = component_agreement(
        str(claude.get("biggest_risk_now") or ""),
        str((gpt.get("top_3_warnings") or [""])[0] if gpt.get("top_3_warnings") else ""),
    )
    c3 = component_agreement(
        str(claude.get("path_to_first_million_note") or ""),
        str(gpt.get("main_bottleneck_to_first_million") or ""),
    )
    c4 = component_agreement(
        str((claude.get("what_is_working") or [""])[0] if claude.get("what_is_working") else ""),
        str(gpt.get("best_live_edge_now") or ""),
    )
    na = str((gpt.get("top_3_next_actions") or [""])[0] if gpt.get("top_3_next_actions") else "")
    c5 = component_agreement(str(claude.get("best_safe_improvement") or ""), na)

    avg = (c1 + c2 + c3 + c4 + c5) / 5.0
    return avg * 100.0


def _section_weight(packet: Dict[str, Any], path: List[str], stale_ok: bool = True) -> float:
    cur: Any = packet
    for p in path:
        cur = cur.get(p) if isinstance(cur, dict) else None
    if cur is None:
        return 0.0
    if isinstance(cur, dict) and cur.get("stale") and not stale_ok:
        return 0.5
    if isinstance(cur, (dict, list)) and len(cur) == 0:
        return 0.5
    return 1.0


def adjust_completeness_for_packet_truth(packet: Dict[str, Any], score: float) -> float:
    """Lower completeness when ``packet_truth`` documents known gaps, conflicts, or thin coverage."""
    pt = packet.get("packet_truth") if isinstance(packet.get("packet_truth"), dict) else {}
    lim = pt.get("limitations") if isinstance(pt.get("limitations"), list) else []
    penalty = min(12.0, float(len(lim)) * 2.5)
    fc = int(pt.get("federation_conflict_count") or 0)
    if fc:
        penalty += min(18.0, float(fc) * 2.0)
    fq = pt.get("field_quality_summary") if isinstance(pt.get("field_quality_summary"), dict) else {}
    if str(fq.get("slippage_coverage_label") or "") == "missing_or_thin":
        penalty += 8.0
    if str(fq.get("net_pnl_coverage_label") or "") == "partial_unknown_net":
        penalty += 10.0
    rep = pt.get("avenue_representation") if isinstance(pt.get("avenue_representation"), dict) else {}
    for _av, row in rep.items():
        if isinstance(row, dict) and (row.get("missing") is True or row.get("representation") == "missing"):
            penalty += 4.0
    return clamp100(max(0.0, score - min(45.0, penalty)))


def compute_packet_completeness_score(packet: Dict[str, Any]) -> float:
    """Packet completeness (0–100) — aligned to ``ai_review_packet_builder`` keys."""
    rs = packet.get("risk_summary") if isinstance(packet.get("risk_summary"), dict) else {}
    ver_ok = 1.0 if isinstance(rs, dict) and "write_verification_failures" in rs else 0.5
    ei = packet.get("execution_intelligence")
    ei_ok = 1.0
    if isinstance(ei, dict):
        ds = ei.get("data_sufficiency") or {}
        if str(ds.get("label") or "") in ("insufficient", "missing", "thin"):
            ei_ok = 0.65
        if ei.get("honesty") and "unavailable" in str(ei.get("honesty")):
            ei_ok = 0.5
    checks = [
        _section_weight(packet, ["capital_state"]),
        _section_weight(packet, ["avenue_state"]),
        _section_weight(packet, ["live_trading_summary"]),
        _section_weight(packet, ["risk_summary"]),
        _section_weight(packet, ["route_summary"]),
        _section_weight(packet, ["shadow_exploration_summary"]),
        _section_weight(packet, ["goal_state"]),
        _section_weight(packet, ["lesson_state"]),
        _section_weight(packet, ["execution_intelligence"]) * ei_ok,
        _section_weight(packet, ["review_context_rank"]),
        ver_ok,
    ]
    return (sum(checks) / 11.0) * 100.0


def compute_anomaly_aggregate_score(
    packet: Dict[str, Any],
    *,
    max_severity: Optional[float] = None,
    recent_avg: float = 0.0,
    repeat_factor: float = 0.0,
    capital_impact: float = 0.0,
    unresolved: float = 0.0,
) -> float:
    """Anomaly aggregate score (0–100)."""
    rs = packet.get("risk_summary") or {}
    if max_severity is None:
        max_severity = float(rs.get("max_anomaly_severity") or rs.get("anomaly_severity") or 0)
    agg = (
        0.30 * clamp100(max_severity)
        + 0.25 * clamp100(recent_avg)
        + 0.20 * clamp100(repeat_factor)
        + 0.15 * clamp100(capital_impact)
        + 0.10 * clamp100(unresolved)
    )
    return min(100.0, agg)


def compute_joint_confidence(
    *,
    claude_confidence_01: float,
    gpt_confidence_01: float,
    packet_completeness_0_100: float,
    agreement_score_0_100: float,
    anomaly_aggregate_0_100: float,
    sample_strength_0_100: float,
    review_integrity: ReviewIntegrityState,
    live_mode_disagreement: bool,
    anomaly_aggregate_for_cap: float,
) -> float:
    """Joint confidence (0.0–1.0); raw formula uses 0–100 inputs for model conf scaled from 0–1."""
    cc = clamp100(claude_confidence_01 * 100.0)
    gc = clamp100(gpt_confidence_01 * 100.0)
    raw = (
        0.22 * cc
        + 0.22 * gc
        + 0.20 * packet_completeness_0_100
        + 0.14 * agreement_score_0_100
        + 0.12 * sample_strength_0_100
        - 0.10 * anomaly_aggregate_0_100
    )
    raw = clamp100(raw)
    out = raw / 100.0

    if review_integrity == ReviewIntegrityState.FAILED:
        return 0.0
    if review_integrity == ReviewIntegrityState.DEGRADED:
        out = min(out, 0.74)
    if packet_completeness_0_100 < 60:
        out = min(out, 0.59)
    if anomaly_aggregate_for_cap > 75:
        out = min(out, 0.49)
    if live_mode_disagreement and anomaly_aggregate_for_cap > 50:
        out = min(out, 0.44)
    return clamp01(out)


def sample_strength_from_packet(packet: Dict[str, Any]) -> float:
    lt = packet.get("live_trading_summary") or {}
    n = int(lt.get("closed_trades_count") or lt.get("trade_count") or 0)
    return sample_strength_from_trade_count(n)
