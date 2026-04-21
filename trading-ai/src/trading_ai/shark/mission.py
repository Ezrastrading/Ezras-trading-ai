"""
EZRAS TRADING AI — CORE MISSION
================================
This is not optional. This is not a suggestion.
This is the singular purpose of this system.

MISSION: $1,000,000 by October 16, 2026.
Starting capital: $200
Days allowed: 180
Required daily growth: ~8.2%

The AI treats every decision through this lens:
Does this action move us closer to $1,000,000?
If not — don't do it.
"""

from __future__ import annotations

import contextvars
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ─── MISSION CONSTANTS ─────────────────────
MISSION_TARGET = 1_000_000.00
MISSION_START_CAPITAL = 200.00
MISSION_START_DATE = "2026-04-16"
MISSION_TARGET_DATE = "2026-10-16"
MISSION_DAYS = 180
REQUIRED_DAILY_GROWTH = 0.082  # 8.2% per day

# ─── PROBABILITY / RISK TIERS ──────────────
# Below this is blocked outright. At/above is allowed but sizing is constrained by tier.
PROB_BLOCK_MIN = 0.63
PROB_TIER_1_MAX = 0.76
PROB_TIER_2_MAX = 0.90

# These caps are intentionally conservative and never override hard safety directives.
# They enforce “high-risk protection / prevention strategy / positioning” at lower tiers
# via smaller allowable sizing rather than changing execution code paths here.
TIER_MAX_RISK_FRACTION = {
    1: 0.05,  # 63–76%: smallest sizing (still allowed)
    2: 0.10,  # 77–90%: moderate sizing
    3: 0.20,  # 90%+: may use full hard cap (but still subject to D1)
}

_MISSION_PROBABILITY_CTX: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "mission_probability_ctx",
    default=None,
)


def mission_probability_get() -> Optional[float]:
    """
    Execution-time mission probability for the current order attempt.

    This is intentionally explicit (set/reset by the execution engine) so the authoritative
    live order guard can enforce probability tiers without guessing.
    """
    return _MISSION_PROBABILITY_CTX.get()


def mission_probability_set(probability: float) -> contextvars.Token:
    return _MISSION_PROBABILITY_CTX.set(float(probability))


def mission_probability_reset(tok: contextvars.Token) -> None:
    _MISSION_PROBABILITY_CTX.reset(tok)


def _probability_tier(probability: float) -> int:
    if probability < PROB_BLOCK_MIN:
        return 0
    if probability <= PROB_TIER_1_MAX:
        return 1
    if probability < PROB_TIER_2_MAX:
        return 2
    return 3

# ─── MISSION DIRECTIVES ────────────────────
# These are absolute. Non-negotiable.
# Every engine, every scanner, every decision
# must be evaluated against these directives.

DIRECTIVES = [
    # DIRECTIVE 1: PROTECT CAPITAL
    {
        "id": "D1",
        "priority": 1,
        "name": "PROTECT CAPITAL",
        "rule": "Never risk more than 20% of "
        "total balance on any single "
        "decision. Capital is the engine. "
        "Without capital there is no mission.",
        "hard_limit": True,
    },
    # DIRECTIVE 2: COMPOUND RELENTLESSLY
    {
        "id": "D2",
        "priority": 2,
        "name": "COMPOUND RELENTLESSLY",
        "rule": "Every profit must be redeployed "
        "immediately. No idle capital. "
        "Compound interest is the path "
        "to $1M. Every $0.01 of profit "
        "left uninvested is a failure.",
        "hard_limit": True,
    },
    # DIRECTIVE 3: HIGH PROBABILITY ONLY
    {
        "id": "D3",
        "priority": 3,
        "name": "HIGH PROBABILITY ONLY",
        "rule": "High probability only. Global: min 63% (tiered risk handling). Kalshi: min 85% "
        "AND entry price 35-65 cents AND 60-120s "
        "before expiry. Coinbase: Gates A/B/C swing only — "
        "night gainers, day momentum, BTC/ETH breakout; "
        "unified exits (trail, target, stop, max hold); "
        "mission sizing and reserve discipline.",
        "hard_limit": True,
    },
    # DIRECTIVE 4: LEARN FROM EVERY LOSS
    {
        "id": "D4",
        "priority": 4,
        "name": "LEARN FROM EVERY LOSS",
        "rule": "Every loss is logged. Every "
        "mistake is a lesson. The system "
        "must never repeat the same "
        "mistake twice. Losses are tuition "
        "paid to the market — extract "
        "maximum learning from each one.",
        "hard_limit": False,
    },
    # DIRECTIVE 5: MAXIMIZE FREQUENCY
    {
        "id": "D5",
        "priority": 5,
        "name": "MAXIMIZE FREQUENCY",
        "rule": "More trades = more compounding when edge is positive. "
        "Deploy within gates and risk limits; "
        "do not churn without signal quality. "
        "Compound realized gains; keep reserve discipline.",
        "hard_limit": False,
    },
    # DIRECTIVE 6: ADAPT INSTANTLY
    {
        "id": "D6",
        "priority": 6,
        "name": "ADAPT INSTANTLY",
        "rule": "If a strategy stops working — "
        "change it immediately. Do not "
        "defend losing approaches. "
        "The market is always right. "
        "Adapt or the mission fails.",
        "hard_limit": False,
    },
    # DIRECTIVE 7: MILESTONE OBSESSION
    {
        "id": "D7",
        "priority": 7,
        "name": "MILESTONE OBSESSION",
        "rule": "Every decision is evaluated: "
        "does this get us closer to the "
        "next milestone? $500 → $1K → "
        "$10K → $100K → $1M. "
        "Celebrate milestones. They prove "
        "the system works.",
        "hard_limit": False,
    },
]

MISSION_STATEMENT = """
╔══════════════════════════════════════════╗
║      EZRAS TRADING AI — MISSION          ║
║  $1,000,000 by October 16, 2026          ║
║  Starting: $200 | Days: 180              ║
║  Required: 8.2% daily growth             ║
║                                          ║
║  THIS IS NOT OPTIONAL.                   ║
║  EVERY TRADE SERVES THIS MISSION.        ║
║  EVERY DECISION IS EVALUATED AGAINST IT. ║
║  THE AI TREATS THIS AS SURVIVAL.         ║
╚══════════════════════════════════════════╝
"""


def get_mission_status(total_balance: float) -> dict:
    now = time.time()
    start_ts = datetime.strptime(MISSION_START_DATE, "%Y-%m-%d").timestamp()
    days_elapsed = (now - start_ts) / 86400
    days_left = max(1, MISSION_DAYS - days_elapsed)

    pct_complete = (total_balance / MISSION_TARGET) * 100

    # Required daily growth to hit target
    if total_balance > 0 and total_balance < MISSION_TARGET:
        required_daily = ((MISSION_TARGET / total_balance) ** (1 / days_left) - 1) * 100
    else:
        required_daily = 0

    # Projected balance at required rate
    projected_30d = total_balance * (1 + required_daily / 100) ** 30
    projected_90d = total_balance * (1 + required_daily / 100) ** 90
    projected_180d = total_balance * (1 + required_daily / 100) ** 180

    # On track?
    expected_now = MISSION_START_CAPITAL * (1 + REQUIRED_DAILY_GROWTH) ** days_elapsed
    on_track = total_balance >= expected_now * 0.8

    return {
        "total_balance": total_balance,
        "target": MISSION_TARGET,
        "pct_complete": pct_complete,
        "days_elapsed": int(days_elapsed),
        "days_left": int(days_left),
        "required_daily_pct": required_daily,
        "on_track": on_track,
        "expected_balance_now": expected_now,
        "variance_pct": ((total_balance - expected_now) / expected_now * 100),
        "projected_30d": projected_30d,
        "projected_90d": projected_90d,
        "projected_180d": projected_180d,
    }


def evaluate_trade_against_mission(
    platform: str,
    product_id: str,
    size_usd: float,
    probability: float,
    total_balance: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Every trade is evaluated against the mission.
    Returns: approved, reason, directive_violated

    ``metadata`` is optional (e.g. gate, strategy, instrument_type for Coinbase spot).
    """
    _ = metadata  # reserved for future rules (sizing by gate, spot vs contract)
    violations = []
    prob_tier = _probability_tier(probability)

    # D1: Never risk > 20% of balance
    if size_usd > total_balance * 0.20:
        violations.append(
            {
                "directive": "D1",
                "reason": f"Size ${size_usd:.2f} > " f"20% of ${total_balance:.2f}",
            }
        )

    # D3: High probability only
    if prob_tier == 0:
        violations.append(
            {
                "directive": "D3",
                "reason": f"Prob {probability:.0%} < {PROB_BLOCK_MIN:.0%} minimum",
            }
        )

    # Tiered sizing protection (applies even when allowed).
    if prob_tier in (1, 2, 3) and total_balance > 0:
        tier_cap = total_balance * TIER_MAX_RISK_FRACTION[prob_tier]
        if size_usd > tier_cap:
            violations.append(
                {
                    "directive": "D3",
                    "reason": (
                        f"Size ${size_usd:.2f} exceeds tier-{prob_tier} cap "
                        f"${tier_cap:.2f} at prob {probability:.0%}"
                    ),
                }
            )

    # Note: Kalshi-specific entry constraints (e.g., 85% min) are enforced in Kalshi
    # trading gates/scanners. Mission-level evaluation stays global + tiered so that
    # mission protection is consistent across venues.

    # D3: Liquid coins only on Coinbase
    if platform == "coinbase":
        low_quality = [
            p
            for p in [product_id]
            if any(x in p for x in ["PEPE", "SHIB", "FLOKI", "ELON", "BONK"])
        ]
        if low_quality:
            violations.append(
                {
                    "directive": "D3",
                    "reason": f"{product_id} is " f"low quality",
                }
            )

    approved = len(violations) == 0

    return {
        "approved": approved,
        "violations": violations,
        "mission_aligned": approved,
        "probability_tier": prob_tier,
        "reason": violations[0]["reason"] if violations else "APPROVED",
    }


def get_mission_briefing(
    total_balance: float,
    todays_pnl: float,
) -> str:
    status = get_mission_status(total_balance)

    urgency = "🚨 CRITICAL" if not status["on_track"] else "✅ ON TRACK"

    return f"""
{MISSION_STATEMENT}
🎯 MISSION STATUS: {urgency}
{'═'*40}
💰 Balance: ${total_balance:,.2f}
📊 Progress: {status['pct_complete']:.3f}% to $1M
📅 Day {status['days_elapsed']} of {MISSION_DAYS}
⏰ Days left: {status['days_left']}

📈 REQUIRED DAILY: {status['required_daily_pct']:.2f}%
📊 TODAY'S P&L: ${todays_pnl:+.4f}
{'✅ Beating target' if todays_pnl > 0 else '🚨 BELOW TARGET — MUST ACCELERATE'}

"""


def get_directives_summary() -> str:
    lines = ["🔒 MISSION DIRECTIVES (HARDWIRED):"]
    for d in DIRECTIVES:
        lock = "🔒" if d["hard_limit"] else "📌"
        lines.append(f"{lock} {d['id']}: {d['name']}")
        lines.append(f"   → {d['rule'][:80]}")
    return "\n".join(lines)


def generate_full_ceo_briefing(
    total_balance: float,
    coinbase_bal: float,
    kalshi_bal: float,
    todays_pnl: float,
    todays_trades: int,
    win_rate: float,
    day_number: int,
    all_time_pnl: float,
    lessons: list,
    recent_trades: list,
) -> list:
    """
    Generates 5 Telegram messages for CEO briefing.
    Called 5x per day. Claude reads all of this.
    Returns list of message strings.
    """
    pct = (total_balance / 1_000_000) * 100
    days_left = max(1, 180 - day_number)
    required_daily = ((1_000_000 / max(1, total_balance)) ** (1 / days_left) - 1) * 100
    on_track = todays_pnl > 0

    if todays_trades > 0:
        avg_per_trade = todays_pnl / todays_trades
    else:
        avg_per_trade = 0

    if todays_pnl > 0:
        days_to_million = int((1_000_000 - total_balance) / todays_pnl)
    else:
        days_to_million = 999

    messages = []

    msg1 = f"""
╔══════════════════════════════════════════╗
║   🎯 MISSION: $1,000,000 by Oct 2026    ║
║   Day {day_number:<3} of 180 | {pct:.4f}% there  ║
║   Balance: ${total_balance:>12,.2f}          ║
║   Required: {required_daily:.2f}%/day              ║
║   Today: ${todays_pnl:>+12.4f}               ║
║   {'✅ ON TRACK — KEEP PUSHING' if on_track else '🚨 BEHIND — MUST ACCELERATE':^40} ║
╚══════════════════════════════════════════╝

💰 BALANCES:
  🟡 Coinbase: ${coinbase_bal:,.2f}
  🔴 Kalshi:   ${kalshi_bal:,.2f}
  📊 Total:    ${total_balance:,.2f}
  📈 All-time P&L: ${all_time_pnl:+,.4f}

⏱️ AT TODAY'S PACE:
  Days to $1M: {days_to_million}
  {'🔥 4 MONTHS POSSIBLE!' if days_to_million <= 120 else '📈 Accelerate to hit 4 months'}
  {'✅ 6 MONTHS ON TRACK' if days_to_million <= 180 else '⚠️ Need to improve pace'}"""
    messages.append(msg1)

    msg2 = f"""
📊 PERFORMANCE ANALYSIS
{'═'*40}
COINBASE GATE SUMMARY
  (stats unavailable)

✅ WHAT WORKED TODAY:
  Trades: {todays_trades}
  Win Rate: {win_rate*100:.1f}%
  Avg profit/trade: ${avg_per_trade:+.4f}

❌ WHAT FAILED TODAY:
  Loss rate: {(1-win_rate)*100:.1f}%

🎯 EFFICIENCY SCORE:
  {'EXCELLENT' if win_rate >= 0.8 else 'GOOD' if win_rate >= 0.6 else 'NEEDS WORK' if win_rate >= 0.4 else 'CRITICAL'}
  ({win_rate*100:.1f}% win rate)"""
    messages.append(msg2)

    msg3 = f"""
📚 RIGHTS / WRONGS / AVOID
{'═'*40}
🔑 NON-NEGOTIABLE RULES:
  • 85%+ prob on Kalshi always
  • Coinbase: Gates A/B/C swing system only
  • 20% reserve at all times (mission never overrides hard safety)
  • Exits: unified engine — trail, targets, stops, timeouts, dawn sweep"""
    messages.append(msg3)

    msg4 = f"""
🔍 STRATEGY
{'═'*40}
1. COINBASE (NTE): prioritize high-quality signals; protect capital; compound wins
2. KALSHI: high probability only; avoid range mistakes; follow TTR constraints"""
    messages.append(msg4)

    msg5 = f"""
🔒 DIRECTIVES (SUMMARY)
{'═'*40}
{get_directives_summary()}
"""
    messages.append(msg5)
    return messages

