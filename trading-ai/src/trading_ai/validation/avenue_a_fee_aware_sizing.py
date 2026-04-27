"""
Fee-aware first-trade sizing for Avenue A Coinbase execution.

Rejects fee-dominated tiny trades and ensures positive net expectancy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoinbaseFeeModel:
    """Coinbase Advanced Trade fee and slippage model."""
    maker_fee_pct: float = 0.0015  # 0.15%
    taker_fee_pct: float = 0.0025  # 0.25%
    estimated_spread_pct: float = 0.0005  # 0.05%
    estimated_slippage_pct: float = 0.0005  # 0.05%
    
    def round_trip_cost_pct(self, assume_maker_entry: bool = True) -> float:
        """Calculate round-trip cost as percentage."""
        entry_fee = self.maker_fee_pct if assume_maker_entry else self.taker_fee_pct
        exit_fee = self.taker_fee_pct  # Assume taker for exit (market order)
        return entry_fee + exit_fee + self.estimated_spread_pct + self.estimated_slippage_pct


@dataclass(frozen=True)
class FirstTradeSizingResult:
    """Result of fee-aware first-trade sizing."""
    ok: bool
    quote_size_usd: float
    reason: str
    meta: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "quote_size_usd": float(self.quote_size_usd),
            "reason": self.reason,
            "meta": dict(self.meta),
        }


def fee_aware_first_trade_size(
    *,
    equity_usd: Optional[float] = None,
    venue_min_notional: float,
    expected_edge_bps: float,
    fee_model: Optional[CoinbaseFeeModel] = None,
    min_net_profit_buffer_usd: float = 0.50,
    assume_maker_entry: bool = True,
    avenue: str = "coinbase",
) -> FirstTradeSizingResult:
    """
    Calculate fee-aware first-trade size for Avenue A.
    
    Rejects trades that are fee-dominated (fees/slippage likely exceed edge).
    
    Args:
        equity_usd: Account equity in USD (if None, fetched from avenue)
        venue_min_notional: Minimum order size from venue (e.g., $2.00)
        expected_edge_bps: Expected edge in basis points
        fee_model: Coinbase fee/slippage model
        min_net_profit_buffer_usd: Minimum net profit after all costs
        assume_maker_entry: Whether entry is maker (limit) or taker (market)
        avenue: The avenue to fetch capital from (default: "coinbase")
    
    Returns:
        FirstTradeSizingResult with sizing decision and detailed metadata
    """
    if fee_model is None:
        fee_model = CoinbaseFeeModel()
    
    # Fetch equity from avenue if not provided
    if equity_usd is None:
        try:
            from trading_ai.core.avenue_capital import get_capital_for_trade
            equity_usd = get_capital_for_trade(avenue)
            if equity_usd == 0.0:
                return FirstTradeSizingResult(
                    False,
                    0.0,
                    "capital_not_available",
                    {
                        "avenue": avenue,
                        "message": f"Capital not available for avenue {avenue}",
                    },
                )
        except Exception as exc:
            return FirstTradeSizingResult(
                False,
                0.0,
                "capital_fetch_error",
                {
                    "avenue": avenue,
                    "error": str(exc),
                },
            )
    
    try:
        eq = float(equity_usd)
    except Exception:
        return FirstTradeSizingResult(
            False,
            0.0,
            "invalid_equity",
            {"equity_usd": equity_usd},
        )
    
    if eq <= 0:
        return FirstTradeSizingResult(
            False,
            0.0,
            "equity_non_positive",
            {"equity_usd": eq},
        )
    
    # Start with 8% of capital as baseline (as per original recommendation)
    baseline_quote = eq * 0.08
    
    # Ensure above venue minimum
    quote = max(float(venue_min_notional), baseline_quote)
    
    # Calculate costs
    round_trip_cost_pct = fee_model.round_trip_cost_pct(assume_maker_entry=assume_maker_entry)
    round_trip_cost_usd = quote * round_trip_cost_pct
    
    # Calculate expected gross profit
    expected_gross_usd = quote * (float(expected_edge_bps) / 10000.0)
    
    # Calculate expected net profit
    expected_net_usd = expected_gross_usd - round_trip_cost_usd
    
    # Fee dominance check
    fee_dominance_ratio = round_trip_cost_usd / expected_gross_usd if expected_gross_usd > 0 else float('inf')
    
    if fee_dominance_ratio >= 1.0:
        return FirstTradeSizingResult(
            False,
            quote,
            "trade_size_fee_dominated",
            {
                "equity_usd": eq,
                "baseline_quote": baseline_quote,
                "venue_min_notional": float(venue_min_notional),
                "quote_usd": quote,
                "expected_edge_bps": float(expected_edge_bps),
                "expected_gross_usd": expected_gross_usd,
                "round_trip_cost_pct": round_trip_cost_pct,
                "round_trip_cost_usd": round_trip_cost_usd,
                "expected_net_usd": expected_net_usd,
                "fee_dominance_ratio": fee_dominance_ratio,
                "maker_fee_pct": fee_model.maker_fee_pct,
                "taker_fee_pct": fee_model.taker_fee_pct,
                "estimated_spread_pct": fee_model.estimated_spread_pct,
                "estimated_slippage_pct": fee_model.estimated_slippage_pct,
                "min_net_profit_buffer_usd": min_net_profit_buffer_usd,
                "message": (
                    "At this account size, Coinbase fees/slippage likely exceed edge. "
                    "Do not place live trade."
                ),
            },
        )
    
    # Net profit buffer check
    if expected_net_usd < min_net_profit_buffer_usd:
        return FirstTradeSizingResult(
            False,
            quote,
            "net_profit_below_buffer",
            {
                "equity_usd": eq,
                "baseline_quote": baseline_quote,
                "venue_min_notional": float(venue_min_notional),
                "quote_usd": quote,
                "expected_edge_bps": float(expected_edge_bps),
                "expected_gross_usd": expected_gross_usd,
                "round_trip_cost_pct": round_trip_cost_pct,
                "round_trip_cost_usd": round_trip_cost_usd,
                "expected_net_usd": expected_net_usd,
                "fee_dominance_ratio": fee_dominance_ratio,
                "min_net_profit_buffer_usd": min_net_profit_buffer_usd,
                "message": (
                    f"Expected net profit ${expected_net_usd:.2f} is below "
                    f"minimum buffer ${min_net_profit_buffer_usd:.2f}"
                ),
            },
        )
    
    # Trade is viable
    return FirstTradeSizingResult(
        True,
        quote,
        "ok",
        {
            "avenue": avenue,
            "equity_usd": eq,
            "baseline_quote": baseline_quote,
            "venue_min_notional": float(venue_min_notional),
            "quote_usd": quote,
            "expected_edge_bps": float(expected_edge_bps),
            "expected_gross_usd": expected_gross_usd,
            "round_trip_cost_pct": round_trip_cost_pct,
            "round_trip_cost_usd": round_trip_cost_usd,
            "expected_net_usd": expected_net_usd,
            "fee_dominance_ratio": fee_dominance_ratio,
            "min_net_profit_buffer_usd": min_net_profit_buffer_usd,
            "maker_fee_pct": fee_model.maker_fee_pct,
            "taker_fee_pct": fee_model.taker_fee_pct,
            "estimated_spread_pct": fee_model.estimated_spread_pct,
            "estimated_slippage_pct": fee_model.estimated_slippage_pct,
            "assume_maker_entry": assume_maker_entry,
            "message": "Trade size has positive net expectancy after fees/slippage",
        },
    )
