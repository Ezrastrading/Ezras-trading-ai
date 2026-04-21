from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.global_layer.gap_models import (
    CandidateValidationResult,
    UniversalGapCandidate,
    validate_candidate_fields,
)
from trading_ai.global_layer.gap_thresholds import GapThresholds, load_gap_thresholds_strict


@dataclass(frozen=True)
class GapEngineDecision:
    should_trade: bool
    rejection_reasons: List[str]
    validation: CandidateValidationResult
    thresholds: Optional[GapThresholds]
    thresholds_diagnostics: Dict[str, Any]
    candidate: Optional[Mapping[str, Any]]


def _thresholds_fail_reasons(candidate: Mapping[str, Any], t: GapThresholds) -> List[str]:
    reasons: List[str] = []
    try:
        if float(candidate["edge_percent"]) < float(t.min_edge_percent):
            reasons.append("edge_below_min")
    except Exception:
        reasons.append("edge_percent_unusable_for_thresholds")
    try:
        if float(candidate["confidence_score"]) < float(t.min_confidence_score):
            reasons.append("confidence_below_min")
    except Exception:
        reasons.append("confidence_score_unusable_for_thresholds")
    try:
        if float(candidate["liquidity_score"]) < float(t.min_liquidity_score):
            reasons.append("liquidity_below_min")
    except Exception:
        reasons.append("liquidity_score_unusable_for_thresholds")

    if t.max_fees_estimate is not None:
        if "fees_estimate" not in candidate or candidate.get("fees_estimate") is None:
            reasons.append("fees_estimate_missing_for_thresholds")
        else:
            try:
                if float(candidate["fees_estimate"]) > float(t.max_fees_estimate):
                    reasons.append("fees_above_max")
            except Exception:
                reasons.append("fees_estimate_unusable_for_thresholds")

    if t.max_slippage_estimate is not None:
        if "slippage_estimate" not in candidate or candidate.get("slippage_estimate") is None:
            reasons.append("slippage_estimate_missing_for_thresholds")
        else:
            try:
                if float(candidate["slippage_estimate"]) > float(t.max_slippage_estimate):
                    reasons.append("slippage_above_max")
            except Exception:
                reasons.append("slippage_estimate_unusable_for_thresholds")

    return reasons


def evaluate_candidate(candidate: Any) -> GapEngineDecision:
    """
    Universal decision gate (fail-closed).

    Requirements enforced:
    - If ANY required candidate field missing → should_trade=False
    - No defaults: thresholds must be explicitly configured
    """
    v = validate_candidate_fields(candidate)
    raw: Optional[Mapping[str, Any]]
    if isinstance(candidate, UniversalGapCandidate):
        raw = candidate.to_dict()
    elif isinstance(candidate, Mapping):
        raw = candidate
    else:
        raw = None

    if not v.ok:
        rej: List[str] = []
        if v.missing_fields:
            rej.append("candidate_missing_fields:" + ",".join(sorted(set(v.missing_fields))))
        if v.errors:
            rej.append("candidate_invalid_fields:" + ",".join(sorted(set(v.errors))))
        return GapEngineDecision(
            should_trade=False,
            rejection_reasons=rej or ["candidate_invalid_or_incomplete"],
            validation=v,
            thresholds=None,
            thresholds_diagnostics={"ok": False, "error": "thresholds_not_evaluated_candidate_invalid"},
            candidate=raw,
        )

    thresholds, tdiag = load_gap_thresholds_strict()
    if thresholds is None or not bool(tdiag.get("ok")):
        why = ["thresholds_not_configured"]
        miss = list(tdiag.get("missing") or [])
        inv = list(tdiag.get("invalid") or [])
        if miss:
            why.append("thresholds_missing_env:" + ",".join(sorted(set(miss))))
        if inv:
            why.append("thresholds_invalid_env:" + ",".join(sorted(set(inv))))
        return GapEngineDecision(
            should_trade=False,
            rejection_reasons=why,
            validation=v,
            thresholds=None,
            thresholds_diagnostics=tdiag,
            candidate=raw,
        )

    assert raw is not None
    thr_reasons = _thresholds_fail_reasons(raw, thresholds)
    if thr_reasons:
        return GapEngineDecision(
            should_trade=False,
            rejection_reasons=thr_reasons,
            validation=v,
            thresholds=thresholds,
            thresholds_diagnostics=tdiag,
            candidate=raw,
        )

    return GapEngineDecision(
        should_trade=True,
        rejection_reasons=[],
        validation=v,
        thresholds=thresholds,
        thresholds_diagnostics=tdiag,
        candidate=raw,
    )


def coinbase_liquidity_score(*, quote_volume_24h_usd: float, proposed_notional_usd: float) -> float:
    try:
        vol = float(quote_volume_24h_usd)
        notional = float(proposed_notional_usd)
    except (TypeError, ValueError):
        return 0.0
    if vol <= 0 or notional <= 0:
        return 0.0
    ratio = notional / max(vol, 1e-12)
    if ratio <= 0.001:
        return 1.0
    if ratio >= 0.02:
        return 0.0
    return max(0.0, min(1.0, (0.02 - ratio) / (0.02 - 0.001)))


def map_coinbase_gap_type(edge_family: str, *, latency_trade: bool) -> Optional[str]:
    ef = (edge_family or "").strip().lower()
    if latency_trade:
        return "price_lag"
    if any(k in ef for k in ("mean_revert", "reversion", "reclaim", "bounce")):
        return "behavioral_gap"
    if any(k in ef for k in ("trend", "momentum", "breakout")):
        return "behavioral_gap"
    if any(k in ef for k in ("vol", "volatility")):
        return "volatility_gap"
    if any(k in ef for k in ("microstructure", "maker", "spread", "book")):
        return "structural_gap"
    return None


def map_coinbase_execution_mode(*, maker_intent: bool, may_fallback_market: bool) -> str:
    if maker_intent and may_fallback_market:
        return "hybrid"
    if maker_intent:
        return "maker"
    return "taker"

