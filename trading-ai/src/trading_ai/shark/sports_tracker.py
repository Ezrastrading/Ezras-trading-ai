"""
Sports betting tracker — MANUAL EXECUTION ONLY.

⚠️  NY Law Compliance: New York prohibits automated sports betting.
    This module provides ANALYSIS and PICKS only.
    All bets must be placed MANUALLY by the operator on FanDuel/DraftKings.
    No automated execution. No API calls to betting platforms.

Picks use the same Kelly sizing and edge-detection logic as prediction markets.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)

# NY compliance constant — referenced in tests
NY_AUTOMATED_BETTING_PROHIBITED = True


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Core analysis ─────────────────────────────────────────────────────────────

def analyze_sports_bet(
    event: str,
    bet_type: str,
    american_odds: int,
    estimated_probability: float,
    bankroll: float,
) -> Dict[str, Any]:
    """
    Analyze a sports betting opportunity using Kelly sizing.

    Args:
        event: Event description e.g. "NYK vs BOS — Game 1"
        bet_type: e.g. "Moneyline NYK", "Over 224.5"
        american_odds: e.g. +150, -110
        estimated_probability: model's true probability (0–1)
        bankroll: current sports_manual avenue capital

    Returns dict with edge, kelly_fraction, recommended_usd, confidence.
    """
    # Convert American odds to decimal
    if american_odds > 0:
        decimal_odds = (american_odds / 100.0) + 1.0
    else:
        decimal_odds = (100.0 / abs(american_odds)) + 1.0

    # Implied probability from the line
    implied_prob = 1.0 / decimal_odds

    # Edge: how much better our estimate is vs the market
    edge = estimated_probability - implied_prob

    # Kelly fraction: f = edge / (decimal_odds - 1)
    b = decimal_odds - 1.0  # net odds
    kelly_fraction = edge / b if (b > 0 and edge > 0) else 0.0

    # Quarter-Kelly for safety (same as prediction market sizing)
    kelly_fraction = min(kelly_fraction * 0.25, 0.10)  # cap at 10% of bankroll

    recommended_usd = round(bankroll * kelly_fraction, 2) if kelly_fraction > 0 else 0.0

    # Confidence score (0-1) based on edge magnitude
    confidence = min(1.0, max(0.0, edge * 5))  # 20% edge = 1.0 confidence

    return {
        "event": event,
        "bet_type": bet_type,
        "american_odds": american_odds,
        "decimal_odds": round(decimal_odds, 4),
        "implied_probability": round(implied_prob, 4),
        "estimated_probability": round(estimated_probability, 4),
        "edge": round(edge, 4),
        "kelly_fraction": round(kelly_fraction, 4),
        "recommended_usd": recommended_usd,
        "confidence": round(confidence, 4),
        "actionable": edge > 0.03,  # minimum 3% edge required
    }


def get_daily_picks(bankroll: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    Return today's top sports betting opportunities for MANUAL placement.

    ⚠️  NY COMPLIANCE: This function performs ANALYSIS ONLY.
        No API calls to FanDuel/DraftKings. No automated execution.
        Operator must place all bets manually.

    In production this would pull from a sports odds API (e.g. The Odds API).
    Currently returns framework structure for operator population.
    """
    if bankroll is None:
        try:
            from trading_ai.shark.avenues import load_avenues
            avenues = load_avenues()
            bankroll = avenues.get("sports_manual", None)
            bankroll = bankroll.current_capital if bankroll else 25.0
        except Exception:
            bankroll = 25.0

    # Framework picks — operator populates with real odds from sportsbooks
    # In production: integrate The Odds API (the-odds-api.com) for live lines
    picks: List[Dict[str, Any]] = []

    # Example structure (would be populated from live odds feed):
    example_events = [
        {
            "event": "TBD — check FanDuel/DraftKings for today's slate",
            "bet_type": "Best available line",
            "american_odds": -110,
            "estimated_probability": 0.52,
        },
    ]

    for ev in example_events:
        analysis = analyze_sports_bet(
            event=ev["event"],
            bet_type=ev["bet_type"],
            american_odds=ev["american_odds"],
            estimated_probability=ev["estimated_probability"],
            bankroll=bankroll,
        )
        if analysis["actionable"]:
            picks.append(analysis)

    return picks


def format_sports_picks_message(picks: List[Dict[str, Any]], bankroll: float) -> str:
    """Format picks as a Telegram-ready message with NY compliance notice."""
    header = (
        "🏈 SPORTS PICKS (MANUAL ONLY)\n"
        "⚠️  Place manually on FanDuel/DraftKings\n"
        f"Bankroll: ${bankroll:.2f}\n\n"
    )
    if not picks:
        return header + "No high-confidence picks today. Skip."

    lines = []
    for i, pick in enumerate(picks[:5], 1):  # top 5
        lines.append(
            f"{i}. {pick['event']}\n"
            f"   Bet: {pick['bet_type']} @ {pick['american_odds']:+d}\n"
            f"   Edge: {pick['edge']*100:.1f}% | Kelly: ${pick['recommended_usd']:.2f}\n"
        )

    footer = (
        "\nLog results with:\n"
        "python -m trading_ai shark sports log-result\n"
        "  --event [id] --outcome win --amount [x]"
    )
    return header + "\n".join(lines) + footer


def log_sports_result(
    event_id: str,
    outcome: str,         # "win" or "loss"
    amount: float,
    pnl: Optional[float] = None,
    *,
    platform: str = "fanduel",
    american_odds: float = -110.0,
) -> None:
    """
    Record a manual sports bet result to the sports_manual avenue.
    Updates avenue P&L via the avenue registry.

    Args:
        event_id: Identifier for the event
        outcome: "win" or "loss"
        amount: Amount wagered
        pnl: Actual P&L (default: amount on win, -amount on loss)
    """
    win = outcome.strip().lower() == "win"
    if pnl is None:
        # Simple win/loss P&L (actual return depends on odds — use amount as proxy)
        pnl = amount if win else -amount

    try:
        from trading_ai.shark.avenues import record_trade_result
        from trading_ai.shark.trade_journal import log_sports_trade

        record_trade_result("sports_manual", pnl=pnl, win=win)
        log_sports_trade(
            platform=platform,
            pick=str(event_id),
            odds=float(american_odds),
            stake=float(amount),
            outcome=str(outcome),
            pnl=float(pnl or 0.0),
        )
        logger.info(
            "Sports result logged: %s %s $%.2f pnl=%.2f",
            event_id, outcome, amount, pnl
        )
    except Exception as exc:
        logger.warning("Failed to log sports result: %s", exc)
