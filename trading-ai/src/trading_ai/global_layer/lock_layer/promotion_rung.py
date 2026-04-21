"""Canonical execution rung ladder — maps to promotion tiers without skipping rungs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Tuple

from trading_ai.global_layer.orchestration_schema import PromotionTier, promotion_tier_index


class ExecutionRung(str, Enum):
    """Paper-to-live ladder (venue-agnostic). Aligned 1:1 with T0–T5 staging."""

    SHADOW = "shadow"
    PAPER = "paper"
    MICRO_LIVE = "micro_live"
    BOUNDED_LIVE = "bounded_live"
    SCALED_LIVE = "scaled_live"


_TIER_TO_RUNG: Dict[str, ExecutionRung] = {
    PromotionTier.T0.value: ExecutionRung.SHADOW,
    PromotionTier.T1.value: ExecutionRung.PAPER,
    PromotionTier.T2.value: ExecutionRung.MICRO_LIVE,
    PromotionTier.T3.value: ExecutionRung.BOUNDED_LIVE,
    PromotionTier.T4.value: ExecutionRung.SCALED_LIVE,
    PromotionTier.T5.value: ExecutionRung.SCALED_LIVE,
}


def execution_rung_for_promotion_tier(tier: str) -> ExecutionRung:
    t = str(tier or PromotionTier.T0.value).strip().upper()
    return _TIER_TO_RUNG.get(t, ExecutionRung.SHADOW)


def assert_no_rung_skip(from_tier: str, to_tier: str) -> Tuple[bool, str]:
    """Enforce single-step tier advances for promotion ladder (no skipping T levels)."""
    a = promotion_tier_index(str(from_tier or "T0"))
    b = promotion_tier_index(str(to_tier or "T0"))
    if b == a:
        return False, "no_change"
    if b == a + 1:
        return True, "ok_single_step"
    if b < a:
        return True, "demotion_or_rollback"
    return False, f"skip_forbidden:{a}->{b}"


def sync_execution_rung_on_bot(bot: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(bot)
    pt = str(out.get("promotion_tier") or PromotionTier.T0.value)
    out["execution_rung"] = execution_rung_for_promotion_tier(pt).value
    return out
