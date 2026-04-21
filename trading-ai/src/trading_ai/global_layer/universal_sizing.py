from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from trading_ai.global_layer.gap_models import UniversalGapCandidate, require_valid_candidate_for_execution, validate_candidate_fields


@dataclass(frozen=True)
class UniversalSizingDecision:
    approved: bool
    recommended_notional: float
    bankroll_fraction: float
    sizing_tier: str  # skip|probe|small|standard|strong
    cap_reason: str
    risk_comment: str


def _f(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def size_from_candidate(
    *,
    candidate: Optional[UniversalGapCandidate],
    equity_usd: float,
    gate_id: str,
    avenue_id: str,
) -> UniversalSizingDecision:
    """
    Fail-closed sizing: if candidate is missing/incomplete, approve=False, size=0.

    Caps:
    - per-trade cap fraction of equity (default 1.5%)
    - min edge/conf/liquidity already enforced in gap_engine (but we still fail closed if missing)
    """
    # Sizing must be fail-closed, but it must not require `must_trade=True` yet.
    # `must_trade` is enforced at the execution guard.
    v = validate_candidate_fields(candidate)
    if not v.ok or candidate is None:
        return UniversalSizingDecision(
            approved=False,
            recommended_notional=0.0,
            bankroll_fraction=0.0,
            sizing_tier="skip",
            cap_reason="candidate_incomplete_or_invalid:" + ",".join(list(v.missing_fields or []) + list(v.errors or [])),
            risk_comment="fail_closed_missing_candidate",
        )
    try:
        eq = float(equity_usd)
    except (TypeError, ValueError):
        eq = 0.0
    if eq <= 0:
        return UniversalSizingDecision(
            approved=False,
            recommended_notional=0.0,
            bankroll_fraction=0.0,
            sizing_tier="skip",
            cap_reason="invalid_equity",
            risk_comment="equity_missing_or_non_positive",
        )

    per_trade_cap = _f("UNIVERSAL_PER_TRADE_CAP_PCT_EQUITY", 0.015)
    per_trade_cap = max(0.0, min(0.05, per_trade_cap))
    cap_usd = eq * per_trade_cap

    # Basic tiering based on confidence*edge; no fake defaults (uses provided fields).
    score = float(candidate.confidence_score) * max(0.0, float(candidate.edge_percent))
    if score < 0.25:
        tier = "probe"
        frac = 0.25
    elif score < 0.60:
        tier = "small"
        frac = 0.50
    elif score < 1.10:
        tier = "standard"
        frac = 0.75
    else:
        tier = "strong"
        frac = 1.0

    notional = max(0.0, min(cap_usd, cap_usd * frac))
    min_notional = _f("UNIVERSAL_MIN_NOTIONAL_USD", 10.0)
    if notional + 1e-9 < min_notional:
        return UniversalSizingDecision(
            approved=False,
            recommended_notional=0.0,
            bankroll_fraction=0.0,
            sizing_tier="skip",
            cap_reason="below_min_notional",
            risk_comment=f"cap_usd={cap_usd:.2f}",
        )

    return UniversalSizingDecision(
        approved=True,
        recommended_notional=float(notional),
        bankroll_fraction=float(notional / eq),
        sizing_tier=tier,
        cap_reason="ok",
        risk_comment=f"{avenue_id}:{gate_id}",
    )

