"""
Gate B — compact failure taxonomy and mapping helpers for operator-facing JSON.

Codes are stable, deterministic, and safe to assert in tests.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

GATE_B_TRUTH_MODEL_VERSION = "gate_b_truth_model_v1"

# --- Failure / no-selection taxonomy (operator + audit) ---
MISSING_MARKET_DATA = "missing_market_data"
STALE_MARKET_DATA = "stale_market_data"
SPREAD_TOO_WIDE_MEASURED = "spread_too_wide_measured"
LIQUIDITY_TOO_THIN = "liquidity_too_thin"
MOMENTUM_TOO_WEAK = "momentum_too_weak"
EXCLUDED_BY_POLICY = "excluded_by_policy"
EXCLUDED_BY_COOLDOWN = "excluded_by_cooldown"
CAPITAL_NOT_AVAILABLE = "capital_not_available"
DUPLICATE_OR_LOCKED_SYMBOL = "duplicate_or_locked_symbol"
STRUCTURAL_CANDIDATE_ERROR = "structural_candidate_error"


def failure_codes_for_data_quality(dq: Mapping[str, Any]) -> List[str]:
    codes: List[str] = []
    for r in dq.get("reject_reasons") or []:
        if r == "stale_quote":
            codes.append(STALE_MARKET_DATA)
        elif r == "inconsistent_bid_ask":
            codes.append(MISSING_MARKET_DATA)
    return codes or [MISSING_MARKET_DATA]


def failure_codes_for_liquidity(liq: Mapping[str, Any]) -> List[str]:
    codes: List[str] = []
    for r in liq.get("reject_reasons") or []:
        if r == "spread_not_measured_fail_closed":
            codes.append(MISSING_MARKET_DATA)
        elif r == "spread_above_max":
            codes.append(SPREAD_TOO_WIDE_MEASURED)
        elif r == "volume_24h_below_min":
            codes.append(LIQUIDITY_TOO_THIN)
        elif r == "book_depth_insufficient":
            codes.append(LIQUIDITY_TOO_THIN)
    return codes or [MISSING_MARKET_DATA]


def failure_codes_for_breakout(br: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for r in br.get("reject_reasons") or []:
        if r in ("momentum_below_threshold",):
            out.append(MOMENTUM_TOO_WEAK)
        else:
            out.append(MOMENTUM_TOO_WEAK)
    return out or [MOMENTUM_TOO_WEAK]


def failure_codes_for_correlation(corr: Mapping[str, Any]) -> List[str]:
    if corr.get("allowed"):
        return []
    return [EXCLUDED_BY_POLICY]


def failure_codes_for_reentry(reasons: Sequence[str]) -> List[str]:
    codes: List[str] = []
    for r in reasons:
        rs = str(r)
        if "cooldown_active" in rs or "negative_lesson" in rs:
            codes.append(EXCLUDED_BY_COOLDOWN)
        elif "new_breakout_not_confirmed" in rs or "momentum_not_reset" in rs:
            codes.append(MOMENTUM_TOO_WEAK)
        else:
            codes.append(EXCLUDED_BY_POLICY)
    return codes or [EXCLUDED_BY_COOLDOWN]


def classify_rejection_kind(failure_codes: Sequence[str]) -> str:
    if not failure_codes:
        return "none"
    if STRUCTURAL_CANDIDATE_ERROR in failure_codes:
        return "data_quality"
    if CAPITAL_NOT_AVAILABLE in failure_codes:
        return "capital_gate"
    if any(x in (MISSING_MARKET_DATA, STALE_MARKET_DATA) for x in failure_codes):
        return "data_quality"
    if any(
        x
        in (
            SPREAD_TOO_WIDE_MEASURED,
            LIQUIDITY_TOO_THIN,
            MOMENTUM_TOO_WEAK,
            EXCLUDED_BY_POLICY,
            EXCLUDED_BY_COOLDOWN,
            DUPLICATE_OR_LOCKED_SYMBOL,
        )
        for x in failure_codes
    ):
        return "market_policy"
    return "data_quality"


def gainer_row_failure_codes(
    *,
    passed: bool,
    category: str,
    spread_meta: Mapping[str, Any],
    filters_failed: Sequence[str],
) -> List[str]:
    """Map gainers-selection row state to taxonomy codes."""
    if passed:
        return []
    if category == "feed_error":
        return [MISSING_MARKET_DATA]
    if category == "market_spread_policy":
        return [SPREAD_TOO_WIDE_MEASURED]
    if category == "missing_or_stale_quote":
        reason = str(spread_meta.get("spread_unavailable_reason") or "")
        if reason.startswith("quote_stale"):
            return [STALE_MARKET_DATA]
        return [MISSING_MARKET_DATA]
    if filters_failed:
        return [STRUCTURAL_CANDIDATE_ERROR]
    return [MISSING_MARKET_DATA]


def market_data_quality_summary_from_gainer_rows(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    measured = 0
    missing = 0
    stale = 0
    for r in rows:
        st = str(r.get("spread_measurement_status") or "")
        if st == "measured":
            measured += 1
        elif str(r.get("spread_unavailable_reason") or "").startswith("quote_stale"):
            stale += 1
        else:
            missing += 1
    return {
        "spread_rows_measured": measured,
        "spread_rows_missing_or_unparsed": missing,
        "spread_rows_stale": stale,
        "note": "Counts are per candidate row in this snapshot, not venue-wide.",
    }
