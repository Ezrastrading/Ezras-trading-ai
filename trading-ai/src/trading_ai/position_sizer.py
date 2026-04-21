"""
Dynamic position sizing (venue-agnostic).

Blocks trades that are not economically viable after estimated fees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SizingResult:
    ok: bool
    quote_size: float
    reason: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "quote_size": float(self.quote_size),
            "reason": self.reason,
            "meta": dict(self.meta),
        }


def size_by_risk_pct(
    *,
    equity_usd: float,
    risk_pct: float,
    venue_min_notional: float,
    est_round_trip_cost_bps: float,
    min_net_profit_usd: float,
    target_move_bps: float,
) -> SizingResult:
    """
    Simplified sizing:
    - quote_size = equity * risk_pct
    - ensure >= venue_min_notional
    - ensure expected net at target clears min_net_profit_usd after costs
    """
    try:
        eq = float(equity_usd)
        rp = float(risk_pct)
    except Exception:
        return SizingResult(False, 0.0, "invalid_inputs", {})
    if eq <= 0:
        return SizingResult(False, 0.0, "equity_non_positive", {"equity_usd": eq})
    if rp <= 0:
        return SizingResult(False, 0.0, "risk_pct_non_positive", {"risk_pct": rp})

    quote = eq * rp
    quote = max(float(venue_min_notional), float(quote))

    cost_bps = float(est_round_trip_cost_bps)
    expected_gross = quote * (float(target_move_bps) / 10000.0)
    expected_cost = quote * (float(cost_bps) / 10000.0)
    expected_net = expected_gross - expected_cost
    if expected_net + 1e-12 < float(min_net_profit_usd):
        return SizingResult(
            False,
            quote,
            "size_not_viable_after_fees",
            {
                "expected_net_at_target_usd": expected_net,
                "min_net_profit_usd": float(min_net_profit_usd),
                "est_round_trip_cost_bps": cost_bps,
                "target_move_bps": float(target_move_bps),
                "equity_usd": eq,
                "risk_pct": rp,
            },
        )

    return SizingResult(
        True,
        quote,
        "ok",
        {
            "equity_usd": eq,
            "risk_pct": rp,
            "venue_min_notional": float(venue_min_notional),
            "est_round_trip_cost_bps": cost_bps,
            "target_move_bps": float(target_move_bps),
            "expected_net_at_target_usd": expected_net,
        },
    )

