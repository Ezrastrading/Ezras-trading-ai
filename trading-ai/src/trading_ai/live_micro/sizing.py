from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LiveMicroSizingDecision:
    ok: bool
    reason: str
    quote_size_usd: float
    details: Dict[str, Any]


def compute_live_micro_quote_size(
    *,
    free_quote: float,
    max_notional_usd: float,
    mission_prob: float,
    mission_max_tier_pct: float,
    exchange_min_notional: float,
    allow_bump_to_min: bool = True,
) -> LiveMicroSizingDecision:
    """
    Single canonical sizing decision for live_micro.
    - free_quote: available quote AFTER reservations (USD or USDC depending on product)
    """
    fq = max(0.0, float(free_quote))
    mx = max(0.0, float(max_notional_usd))
    min_n = max(0.0, float(exchange_min_notional))
    mp = float(mission_prob)
    cap = max(0.0, min(0.50, float(mission_max_tier_pct)))

    if mx <= 0.0:
        return LiveMicroSizingDecision(False, "missing_or_invalid_max_notional_usd", 0.0, {"max_notional_usd": mx})
    if fq <= 0.0:
        return LiveMicroSizingDecision(False, "insufficient_free_quote_after_reservations", 0.0, {"free_quote": fq})

    tier_cap = fq * cap
    proposed = min(mx, tier_cap)
    if proposed + 1e-9 < min_n:
        # Three distinct cases:
        # 1) free quote cannot meet venue minimum (hard stop)
        if fq + 1e-9 < min_n:
            return LiveMicroSizingDecision(
                False,
                "insufficient_free_quote_for_min",
                0.0,
                {
                    "free_quote": fq,
                    "tier_cap": tier_cap,
                    "mission_cap_used": cap,
                    "mission_prob": mp,
                    "max_notional_usd": mx,
                    "required_min": min_n,
                    "proposed": proposed,
                },
            )
        # 2) tier cap is below min, but we can safely bump to venue min within hard caps
        if allow_bump_to_min and min_n <= fq + 1e-9 and min_n <= mx + 1e-9:
            return LiveMicroSizingDecision(
                True,
                "tier_cap_below_min_but_bumped_to_min",
                float(min_n),
                {
                    "free_quote": fq,
                    "tier_cap": tier_cap,
                    "mission_cap_used": cap,
                    "mission_prob": mp,
                    "max_notional_usd": mx,
                    "required_min": min_n,
                    "proposed": proposed,
                    "final": float(min_n),
                },
            )
        # 3) cannot bump: suppress product under current tiering
        return LiveMicroSizingDecision(
            False,
            "tier_cap_below_min_and_suppressed",
            0.0,
            {
                "free_quote": fq,
                "tier_cap": tier_cap,
                "mission_cap_used": cap,
                "mission_prob": mp,
                "max_notional_usd": mx,
                "required_min": min_n,
                "proposed": proposed,
            },
        )

    return LiveMicroSizingDecision(
        True,
        "ok",
        float(proposed),
        {
            "free_quote": fq,
            "tier_cap": tier_cap,
            "mission_cap_used": cap,
            "mission_prob": mp,
            "max_notional_usd": mx,
            "required_min": min_n,
            "proposed": proposed,
        },
    )

