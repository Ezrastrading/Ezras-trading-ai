"""Strict JSON validation for Claude / GPT review outputs."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def extract_json_dict(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
        return raw if isinstance(raw, dict) else None
    except json.JSONDecodeError:
        return None

LIVE_MODES = frozenset({"normal", "caution", "paused"})
REVIEW_TYPES = frozenset({"morning", "midday", "eod", "exception"})

# Contract top-level keys only (extras stripped after successful validation).
CLAUDE_OUTPUT_KEYS = frozenset(
    {
        "review_id",
        "packet_id",
        "review_type",
        "generated_at",
        "what_is_working",
        "what_is_not_working",
        "biggest_risk_now",
        "most_fragile_part_of_system",
        "best_safe_improvement",
        "worst_live_behavior_to_cut",
        "best_shadow_candidate_to_watch",
        "capital_preservation_note",
        "path_to_first_million_note",
        "risk_mode_recommendation",
        "confidence_score",
        "avenue_actions",
        "capital_allocation_actions",
        "scaling_actions",
        "strategy_actions",
        "goal_progress_actions",
        "risk_reduction_actions",
        "advisory_explanations",
        "execution_intelligence_confidence",
    }
)
GPT_OUTPUT_KEYS = frozenset(
    {
        "review_id",
        "packet_id",
        "review_type",
        "generated_at",
        "top_3_decisions",
        "top_3_warnings",
        "top_3_next_actions",
        "live_status_recommendation",
        "best_live_edge_now",
        "weakest_live_edge_now",
        "best_growth_opportunity",
        "main_bottleneck_to_first_million",
        "short_ceo_note",
        "confidence_score",
        "avenue_actions",
        "capital_allocation_actions",
        "scaling_actions",
        "strategy_actions",
        "goal_progress_actions",
        "risk_reduction_actions",
        "advisory_explanations",
        "execution_intelligence_confidence",
    }
)


def whitelist_model_output(d: Dict[str, Any], allowed: frozenset) -> Dict[str, Any]:
    """Drop unknown top-level keys so storage matches the contract."""
    return {k: v for k, v in d.items() if k in allowed}


def _is_str_list(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def _apply_ei_defaults(d: Dict[str, Any]) -> None:
    for k in (
        "avenue_actions",
        "capital_allocation_actions",
        "scaling_actions",
        "strategy_actions",
        "goal_progress_actions",
        "risk_reduction_actions",
        "advisory_explanations",
    ):
        d.setdefault(k, [])
    d.setdefault("execution_intelligence_confidence", float(d.get("confidence_score") or 0.4))


def validate_claude_output(d: Dict[str, Any], *, packet_id: str, review_type: str) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    _apply_ei_defaults(d)
    required = (
        "what_is_working",
        "what_is_not_working",
        "biggest_risk_now",
        "most_fragile_part_of_system",
        "best_safe_improvement",
        "worst_live_behavior_to_cut",
        "best_shadow_candidate_to_watch",
        "capital_preservation_note",
        "path_to_first_million_note",
        "risk_mode_recommendation",
        "confidence_score",
        "avenue_actions",
        "capital_allocation_actions",
        "scaling_actions",
        "strategy_actions",
        "goal_progress_actions",
        "risk_reduction_actions",
        "advisory_explanations",
        "execution_intelligence_confidence",
    )
    for k in required:
        if k not in d:
            errs.append(f"missing:{k}")
    if d.get("packet_id") != packet_id:
        errs.append("packet_id_mismatch")
    if str(d.get("review_type") or "") != review_type:
        errs.append("review_type_mismatch")
    if not _is_str_list(d.get("what_is_working")):
        errs.append("what_is_working_not_string_list")
    if not _is_str_list(d.get("what_is_not_working")):
        errs.append("what_is_not_working_not_string_list")
    rm = str(d.get("risk_mode_recommendation") or "")
    if rm not in LIVE_MODES:
        errs.append("invalid_risk_mode")
    try:
        cf = float(d.get("confidence_score"))
        if cf < 0 or cf > 1:
            errs.append("confidence_out_of_range")
    except (TypeError, ValueError):
        errs.append("confidence_not_float")
    for sk in (
        "biggest_risk_now",
        "most_fragile_part_of_system",
        "best_safe_improvement",
        "worst_live_behavior_to_cut",
        "best_shadow_candidate_to_watch",
        "capital_preservation_note",
        "path_to_first_million_note",
    ):
        if sk in d and not isinstance(d.get(sk), str):
            errs.append(f"{sk}_not_string")
    for lk in (
        "avenue_actions",
        "capital_allocation_actions",
        "scaling_actions",
        "strategy_actions",
        "goal_progress_actions",
        "risk_reduction_actions",
        "advisory_explanations",
    ):
        if not _is_str_list(d.get(lk)):
            errs.append(f"{lk}_not_string_list")
    try:
        ec = float(d.get("execution_intelligence_confidence"))
        if ec < 0 or ec > 1:
            errs.append("execution_intelligence_confidence_out_of_range")
    except (TypeError, ValueError):
        errs.append("execution_intelligence_confidence_not_float")
    return len(errs) == 0, errs


def validate_gpt_output(d: Dict[str, Any], *, packet_id: str, review_type: str) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    _apply_ei_defaults(d)
    required = (
        "top_3_decisions",
        "top_3_warnings",
        "top_3_next_actions",
        "live_status_recommendation",
        "best_live_edge_now",
        "weakest_live_edge_now",
        "best_growth_opportunity",
        "main_bottleneck_to_first_million",
        "short_ceo_note",
        "confidence_score",
        "avenue_actions",
        "capital_allocation_actions",
        "scaling_actions",
        "strategy_actions",
        "goal_progress_actions",
        "risk_reduction_actions",
        "advisory_explanations",
        "execution_intelligence_confidence",
    )
    for k in required:
        if k not in d:
            errs.append(f"missing:{k}")
    if d.get("packet_id") != packet_id:
        errs.append("packet_id_mismatch")
    if str(d.get("review_type") or "") != review_type:
        errs.append("review_type_mismatch")
    for k in ("top_3_decisions", "top_3_warnings", "top_3_next_actions"):
        if not _is_str_list(d.get(k)):
            errs.append(f"{k}_not_string_list")
    ls = str(d.get("live_status_recommendation") or "")
    if ls not in LIVE_MODES:
        errs.append("invalid_live_status")
    try:
        cf = float(d.get("confidence_score"))
        if cf < 0 or cf > 1:
            errs.append("confidence_out_of_range")
    except (TypeError, ValueError):
        errs.append("confidence_not_float")
    for lk in (
        "avenue_actions",
        "capital_allocation_actions",
        "scaling_actions",
        "strategy_actions",
        "goal_progress_actions",
        "risk_reduction_actions",
        "advisory_explanations",
    ):
        if not _is_str_list(d.get(lk)):
            errs.append(f"{lk}_not_string_list")
    try:
        ec = float(d.get("execution_intelligence_confidence"))
        if ec < 0 or ec > 1:
            errs.append("execution_intelligence_confidence_out_of_range")
    except (TypeError, ValueError):
        errs.append("execution_intelligence_confidence_not_float")
    return len(errs) == 0, errs


def strip_internal_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    drop = {
        "stub",
        "error",
        "_validation_ok",
        "_validation_errors",
        "_repair_used",
        "_raw_response_truncated",
    }
    return {k: v for k, v in d.items() if k not in drop}


# --- Canonical federated trade row (observability; not required for organism routing) ---
#
# {
#   "trade_id": str,
#   "avenue": str,
#   "timestamp_open": float | str,
#   "timestamp_close": float | str,
#   "net_pnl": float | None,
#   "fees": float | None,
#   "slippage_bps": float | None,  # or entry_slippage_bps / exit_slippage_bps per venue
#   "execution_latency_ms": int | None,
#   "strategy_class": str | None,
#   "route_bucket": str | None,
#   "truth_provenance": dict,
# }
#
# Strategy, edge, latency, and fees are optional metadata — federation must merge without
# requiring them for numeric truth (see trade_truth module).
CANONICAL_TRADE_RECORD_METADATA_KEYS = frozenset(
    {
        "strategy_class",
        "route_bucket",
        "router_bucket",
        "route_label",
        "expected_edge_bps",
        "expected_net_edge_bps",
        "execution_latency_ms",
        "entry_slippage_bps",
        "exit_slippage_bps",
        "fees",
        "fees_usd",
        "fees_paid",
    }
)
