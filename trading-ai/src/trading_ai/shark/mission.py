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

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── MISSION CONSTANTS ─────────────────────
MISSION_TARGET = 1_000_000.00
MISSION_START_CAPITAL = 200.00
MISSION_START_DATE = "2026-04-16"
MISSION_TARGET_DATE = "2026-10-16"
MISSION_DAYS = 180
REQUIRED_DAILY_GROWTH = 0.082  # 8.2% per day

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
        "rule": "Kalshi: min 85% probability. "
        "Coinbase: only liquid coins "
        "with $500K+ daily volume. "
        "Never gamble. Only take trades "
        "with clear edge.",
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
        "rule": "More trades = more compounding. "
        "20 positions always open. "
        "5 minute cycles. 12 cycles/hour. "
        "Never leave capital idle. "
        "Every second capital sits unused "
        "is a missed compounding opportunity.",
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
) -> dict:
    """
    Every trade is evaluated against the mission.
    Returns: approved, reason, directive_violated
    """
    violations = []

    # D1: Never risk > 20% of balance
    if size_usd > total_balance * 0.20:
        violations.append(
            {
                "directive": "D1",
                "reason": f"Size ${size_usd:.2f} > " f"20% of ${total_balance:.2f}",
            }
        )

    # D3: High probability only
    if platform == "kalshi" and probability < 0.85:
        violations.append(
            {
                "directive": "D3",
                "reason": f"Kalshi prob {probability:.0%}" f" < 85% minimum",
            }
        )

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

🔮 PROJECTIONS (at required rate):
 30 days:  ${status['projected_30d']:>12,.0f}
 90 days:  ${status['projected_90d']:>12,.0f}
180 days: ${status['projected_180d']:>12,.0f}

{'✅ ON TRACK FOR $1M' if status['on_track'] else '🚨 FALLING BEHIND — ADJUST STRATEGY NOW'}
{'═'*40}"""


def get_directives_summary() -> str:
    lines = ["🔒 MISSION DIRECTIVES (HARDWIRED):"]
    for d in DIRECTIVES:
        lock = "🔒" if d["hard_limit"] else "📌"
        lines.append(f"{lock} {d['id']}: {d['name']}")
        lines.append(f"   → {d['rule'][:80]}")
    return "\n".join(lines)
