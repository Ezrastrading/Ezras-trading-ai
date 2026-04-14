"""Opportunity scoring + tier assignment — no time-of-day gating."""

from __future__ import annotations

import logging
from typing import Sequence

from trading_ai.shark.models import (
    HuntSignal,
    HuntType,
    MarketSnapshot,
    OpportunityTier,
    ScoredOpportunity,
)
from trading_ai.shark.state import BAYES

logger = logging.getLogger(__name__)


def liquidity_score_ratio(market: MarketSnapshot) -> float:
    """available_volume / required_position, capped at 1.0 (Section 3)."""
    req = max(market.required_position_dollars, 1e-6)
    vol = market.volume_24h
    return max(0.0, min(1.0, vol / req))


def resolution_speed_score(ttr_seconds: float) -> float:
    """Bucketed by time to resolution (Section 3)."""
    h = ttr_seconds / 3600.0
    if h < 1.0:
        return 1.0
    if h < 4.0:
        return 0.8
    if h < 24.0:
        return 0.6
    return 0.3


def score_opportunity(
    market: MarketSnapshot,
    hunts: Sequence[HuntSignal],
    *,
    strategy_key: str = "shark_default",
) -> ScoredOpportunity:
    edge_size = max(h.edge_after_fees for h in hunts) if hunts else 0.0
    confidence = sum(h.confidence for h in hunts) / max(1, len(hunts))
    liq = liquidity_score_ratio(market)
    rs = resolution_speed_score(market.time_to_resolution_seconds)
    spw = BAYES.strategy_performance_weight(strategy_key)
    score = (
        edge_size * 0.35
        + confidence * 0.25
        + liq * 0.20
        + rs * 0.10
        + spw * 0.10
    )
    tier, mult = _tier_from_hunts_and_score(hunts, score)
    out = ScoredOpportunity(
        market=market,
        hunts=list(hunts),
        edge_size=edge_size,
        confidence=confidence,
        liquidity_score=liq,
        resolution_speed_score=rs,
        strategy_performance_weight=spw,
        score=score,
        tier=tier,
        tier_sizing_multiplier=mult,
    )
    logger.info(
        "Scored: %s tier=%s score=%.3f",
        market.market_id,
        out.tier.value,
        out.score,
    )
    return out


def _tier_from_hunts_and_score(hunts: Sequence[HuntSignal], score: float) -> tuple[OpportunityTier, float]:
    if any(h.hunt_type == HuntType.PURE_ARBITRAGE for h in hunts):
        return OpportunityTier.TIER_A, 1.3
    if any(
        h.hunt_type
        in (
            HuntType.CRYPTO_SCALP,
            HuntType.NEAR_RESOLUTION,
            HuntType.ORDER_BOOK_IMBALANCE,
        )
        for h in hunts
    ):
        return OpportunityTier.TIER_B, 1.0
    if len(hunts) >= 2:
        return OpportunityTier.TIER_A, 1.3
    if len(hunts) == 1 and score >= 0.48:
        return OpportunityTier.TIER_B, 1.0
    if len(hunts) == 1 and score >= 0.30:
        return OpportunityTier.TIER_C, 0.7
    return OpportunityTier.BELOW_THRESHOLD, 0.0
