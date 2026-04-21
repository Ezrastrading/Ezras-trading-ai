"""
Explicit profit / loss explanations per avenue — measurement-facing language only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from trading_ai.knowledge.avenue_models import describe_avenue

PRINCIPLES: List[Dict[str, Any]] = [
    {"id": "execution_first", "claim": "No edge survives bad execution.", "tags": ["execution", "edge"]},
    {"id": "post_fee_expectancy", "claim": "Post-fee expectancy matters more than gross PnL.", "tags": ["fees", "expectancy"]},
    {"id": "discipline_over_frequency", "claim": "Discipline beats frequency.", "tags": ["discipline"]},
    {"id": "no_trade_better", "claim": "No-trade is better than a bad trade.", "tags": ["discipline", "risk"]},
    {"id": "sample_size", "claim": "Sample size matters for any performance claim.", "tags": ["statistics"]},
    {"id": "drawdown_normal", "claim": "Drawdown is normal; size and survival matter.", "tags": ["risk", "drawdown"]},
    {"id": "capital_preservation", "claim": "Capital preservation precedes scaling.", "tags": ["risk", "capital"]},
]


def explain_how_profit_is_made(avenue: str, trade: Optional[Mapping[str, Any]] = None) -> str:
    aid = (avenue or "").strip().lower()
    t = trade or {}
    if aid == "coinbase":
        return (
            "Spot profit: realized when exit price (minus fees/slippage) improves vs entry "
            f"for side={t.get('side', 'n/a')}; path: favorable price movement after entry."
        )
    if aid == "kalshi":
        return (
            "Prediction profit: contracts settle to $1 or $0; you profit when your side's "
            "implied probability was wrong vs realized outcome, after fees — "
            f"position={t.get('side', 'yes/no')}."
        )
    if aid in ("options", "option"):
        return (
            "Options profit: long premium benefits from favorable directional move and/or vol expansion "
            "relative to premium paid; short premium benefits from decay and stable path — "
            f"structure={t.get('structure', 'unspecified')}."
        )
    return f"Unknown avenue {avenue}: describe venue-specific settlement and fees first."


def explain_how_loss_happened(avenue: str, trade: Optional[Mapping[str, Any]] = None) -> str:
    aid = (avenue or "").strip().lower()
    t = trade or {}
    if aid == "coinbase":
        return (
            "Spot loss: adverse price move after entry and/or costs (fees, slippage) exceeding gross move — "
            f"exit_reason={t.get('exit_reason', 'n/a')}."
        )
    if aid == "kalshi":
        return (
            "Prediction loss: held contracts that expired worthless vs entry price, or sold winner too early — "
            f"settlement={t.get('settlement', 'unknown')}."
        )
    if aid in ("options", "option"):
        return (
            "Options loss: premium paid decayed without compensating move; or short vol structures blew out — "
            f"exit={t.get('exit', 'n/a')}."
        )
    return f"Unknown avenue {avenue}: map loss to settlement and fee drag."


def market_trade_reasoning(trade: Mapping[str, Any]) -> Dict[str, Any]:
    """Per-trade narrative hooks — hypotheses, not assertions."""
    venue = str(trade.get("venue") or trade.get("exchange") or "unknown")
    return {
        "why_this_market_exists": "Venues match buyers/sellers and coordinate settlement.",
        "what_participants_likely_doing": "Liquidity providers quote; speculators take directional risk.",
        "what_edge_might_exist": "Temporary mispricing vs fair value when costs are low enough.",
        "what_could_invalidate_edge": "Regime shift, fee change, liquidity withdrawal, or model error.",
        "venue": venue,
    }


def avenue_explanation_card(avenue: str) -> Dict[str, Any]:
    d = describe_avenue(avenue)
    aid = str(d.get("avenue", avenue)).lower()
    mechanics = d.get("profit_summary") or d.get("class") or ""
    return {
        "avenue": aid,
        "mechanics": mechanics,
        "profit_driver": explain_how_profit_is_made(aid),
        "main_failure_mode": explain_how_loss_happened(aid),
        "execution_requirements": [
            "Accurate fee and latency assumptions",
            "Book depth appropriate for size",
            "Halt/governance alignment",
        ],
    }
