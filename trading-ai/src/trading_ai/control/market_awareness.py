"""
Market quality gates for mode changes — block aggressive scaling when liquidity/regime deteriorate
even if recent PnL looks good.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


def evaluate_market_quality_for_scaling(
    *,
    liquidity_health: float,
    slippage_health: float,
    market_regime: str,
    market_chop_score: float,
) -> Dict[str, Any]:
    """
    Returns whether scaling up (CONFIDENT / AGGRESSIVE_CONFIRMED) is *allowed* by market structure.

    Strong PnL must not override deteriorating microstructure.
    """
    allow = True
    reasons: list[str] = []
    if liquidity_health < 0.35:
        allow = False
        reasons.append("liquidity_too_weak_for_scale")
    if slippage_health < 0.35:
        allow = False
        reasons.append("slippage_environment_hostile")
    if market_regime == "chop" or market_chop_score > 0.75:
        allow = False
        reasons.append("chop_or_disorderly_regime")
    mq = max(0.0, min(1.0, 0.45 * liquidity_health + 0.35 * slippage_health + 0.2 * (1.0 - market_chop_score)))
    return {
        "market_quality_allows_aggressive_scale": allow,
        "market_quality_score": mq,
        "block_reasons": reasons,
    }


def combine_pnl_and_market_for_scale(
    *,
    pnl_evidence_strong: bool,
    market_allows: bool,
) -> Tuple[bool, str]:
    if not pnl_evidence_strong:
        return False, "insufficient_pnl_evidence"
    if not market_allows:
        return False, "market_quality_blocks_scale_despite_pnl"
    return True, "ok"
