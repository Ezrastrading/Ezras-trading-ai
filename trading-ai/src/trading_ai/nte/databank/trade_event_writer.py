"""Assemble validated trade records and persist raw events (used by TradeIntelligenceDatabank)."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

from trading_ai.nte.databank.databank_schema import merge_defaults, validate_trade_event_payload
from trading_ai.nte.databank.trade_score_engine import compute_scores_for_trade, suggest_reward_penalty_deltas


def validate_and_build_record(raw: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], list]:
    """
    Validate payload, merge defaults, compute scores.
    Returns (full_record, scores, validation_errors).
    """
    errs = validate_trade_event_payload(raw)
    merged = merge_defaults(dict(raw))
    scores = compute_scores_for_trade(merged)
    rd = suggest_reward_penalty_deltas(scores, merged)
    merged["reward_delta"] = raw.get("reward_delta", rd["reward_delta"])
    merged["penalty_delta"] = raw.get("penalty_delta", rd["penalty_delta"])
    for k, v in scores.items():
        merged[k] = v
    return merged, scores, errs
