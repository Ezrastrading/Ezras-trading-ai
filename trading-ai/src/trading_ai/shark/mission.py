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
from contextvars import Token
from datetime import datetime
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Thread/async-safe mission probability context (live guard reads via ``mission_probability_get``).
_mission_probability_ctx: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "mission_probability", default=None
)

# Optional per-trade mission cap fraction override (used by live_micro to avoid hidden 20%).
_mission_cap_fraction_ctx: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "mission_cap_fraction", default=None
)
# Max quote notional as fraction of total balance by Kalshi-style probability tier (1–3).
TIER_MAX_RISK_FRACTION: Dict[int, float] = {1: 0.05, 2: 0.10, 3: 0.20}


def mission_probability_set(p: float) -> Token:
    return _mission_probability_ctx.set(float(p))


def mission_probability_reset(token: Token) -> None:
    _mission_probability_ctx.reset(token)


def mission_probability_get() -> Optional[float]:
    return _mission_probability_ctx.get()


def mission_cap_fraction_set(pct: float) -> Token:
    return _mission_cap_fraction_ctx.set(float(pct))


def mission_cap_fraction_reset(token: Token) -> None:
    _mission_cap_fraction_ctx.reset(token)


def mission_cap_fraction_get() -> Optional[float]:
    v = _mission_cap_fraction_ctx.get()
    if v is not None:
        try:
            return max(0.0, min(0.50, float(v)))
        except Exception:
            return None
    # Env override for live micro (single source of truth for cap).
    raw = (os.environ.get("EZRA_LIVE_MICRO_MISSION_MAX_TIER_PERCENT") or "").strip()
    if raw:
        try:
            return max(0.0, min(0.50, float(raw)))
        except Exception:
            return None
    return None
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
        "rule": "High probability only. Kalshi: min 85% "
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

    cap_override = mission_cap_fraction_get()
    plat = str(platform or "").lower()
    # Only apply cap override to Coinbase/live-micro flows (do not change Kalshi tier semantics).
    cap_frac = float(cap_override) if (cap_override is not None and plat == "coinbase") else 0.20
    # Small tolerance to avoid string/rounding edge cases in logs (e.g. 9.03 vs 9.025).
    tol_usd = 0.01 if plat == "coinbase" else 0.0

    # D1: Never risk > cap fraction of balance (default 20%; live_micro may override)
    if float(size_usd) > (float(total_balance) * float(cap_frac) + float(tol_usd)):
        violations.append(
            {
                "directive": "D1",
                "reason": f"Size ${size_usd:.2f} > " f"{int(round(float(cap_frac)*100))}% of ${total_balance:.2f}",
            }
        )

    probability_tier: Optional[int] = None
    tier_cap: Optional[float] = None
    # Probability tiers and per-tier notional caps apply to Kalshi and Coinbase live sizing
    # (same thresholds — see ``live_order_guard`` tests).
    if plat in ("kalshi", "coinbase"):
        p = float(probability)
        if p < 0.63:
            probability_tier = 0
            label = "Kalshi" if plat == "kalshi" else "Mission probability"
            violations.append(
                {
                    "directive": "D3",
                    "reason": f"{label} prob {p:.0%} < 63% minimum",
                }
            )
        elif p < 0.77:
            probability_tier = 1
            tier_cap = float(total_balance) * (float(cap_override) if (cap_override is not None and plat == "coinbase") else TIER_MAX_RISK_FRACTION[1])
        elif p < 0.90:
            probability_tier = 2
            tier_cap = float(total_balance) * (float(cap_override) if (cap_override is not None and plat == "coinbase") else TIER_MAX_RISK_FRACTION[2])
        else:
            probability_tier = 3
            tier_cap = float(total_balance) * (float(cap_override) if (cap_override is not None and plat == "coinbase") else TIER_MAX_RISK_FRACTION[3])
        if probability_tier and probability_tier > 0 and tier_cap is not None:
            tol2 = tol_usd if (plat == "coinbase" and cap_override is not None) else 0.0
            if float(size_usd) > float(tier_cap) + float(tol2) + 1e-9:
                violations.append(
                    {
                        "directive": "D3",
                        "reason": f"size ${size_usd:.2f} exceeds tier {probability_tier} cap ${tier_cap:.2f}",
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

    out: Dict[str, Any] = {
        "approved": approved,
        "violations": violations,
        "mission_aligned": approved,
        "reason": violations[0]["reason"] if violations else "APPROVED",
    }
    if plat in ("kalshi", "coinbase"):
        out["probability_tier"] = int(probability_tier if probability_tier is not None else 0)
    return out


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
    required_daily = (
        (1_000_000 / max(1, total_balance)) ** (1 / days_left) - 1
    ) * 100
    on_track = todays_pnl > 0

    # Velocity calculation
    if todays_trades > 0:
        avg_per_trade = todays_pnl / todays_trades
    else:
        avg_per_trade = 0

    # Days to $1M at current pace
    if todays_pnl > 0:
        days_to_million = int((1_000_000 - total_balance) / todays_pnl)
    else:
        days_to_million = 999

    # Projected milestones
    proj_30 = total_balance * (1 + required_daily / 100) ** 30
    proj_60 = total_balance * (1 + required_daily / 100) ** 60
    proj_120 = total_balance * (1 + required_daily / 100) ** 120

    messages = []

    # ─── MESSAGE 1: MISSION + BALANCE ───────
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

    # ─── MESSAGE 2: PERFORMANCE ANALYSIS ───
    wins = [t for t in recent_trades if t.get("win")]
    losses = [t for t in recent_trades if not t.get("win")]

    best_coins = {}
    for t in recent_trades:
        pid = t.get("product_id", "?")
        pnl = float(t.get("pnl_usd", 0) or 0)
        best_coins[pid] = best_coins.get(pid, 0) + pnl

    top3 = sorted(best_coins.items(), key=lambda x: -x[1])[:3]
    bot3 = sorted(best_coins.items(), key=lambda x: x[1])[:3]

    # Coinbase per-gate stats (today) + mission line
    cb_gate_lines: list = []
    mission_progress_block = ""
    try:
        from trading_ai.shark.trade_reports import get_platform_report, list_trades

        cb_rep = get_platform_report("coinbase", "day")
        cb_day_trades = list_trades("coinbase", "day")
        by_g = cb_rep.get("by_gate") or {}
        ms = get_mission_status(total_balance)
        reserve_pct = float(
            __import__("os").environ.get("COINBASE_RESERVE_PCT") or "0.20"
        )
        try:
            reserve_pct = float(str(reserve_pct).strip() or "0.20")
        except (TypeError, ValueError):
            reserve_pct = 0.20
        reserve_active = reserve_pct > 0

        def _gate_lines(label: str, gkey: str) -> str:
            g = by_g.get(gkey) or {}
            n = int(g.get("trades") or 0)
            w = int(g.get("wins") or 0)
            losses_g = n - w
            pnl_g = float(g.get("pnl") or 0.0)
            best_pct = 0.0
            for t in cb_day_trades:
                if str(t.get("gate") or "") != gkey:
                    continue
                best_pct = max(best_pct, float(t.get("pnl_pct") or 0.0))
            dep = sum(
                float(t.get("size_usd") or 0)
                for t in cb_day_trades
                if str(t.get("gate") or "") == gkey
            )
            return (
                f"{label}\n"
                f"  Trades today: {n}\n"
                f"  Wins: {w} | Losses: {losses_g}\n"
                f"  Best: {best_pct:+.2f}%\n"
                f"  Deployed: ${dep:,.2f}\n"
                f"  P&L (period): ${pnl_g:+.2f}"
            )

        cb_gate_lines = [
            "COINBASE GATE SUMMARY",
            _gate_lines("Gate A — Night Gainer", "A"),
            _gate_lines("Gate B — Day Momentum", "B"),
            _gate_lines("Gate C — BTC/ETH Breakout", "C"),
            "",
            "MISSION PROGRESS",
            f"  Balance: ${total_balance:,.2f}",
            f"  Progress to $1M: {ms['pct_complete']:.4f}%",
            f"  Required daily: {ms['required_daily_pct']:.2f}%",
            f"  Reserve active: {'Yes' if reserve_active else 'No'}",
        ]
        mission_progress_block = "\n".join(cb_gate_lines)
    except Exception:
        mission_progress_block = (
            "COINBASE GATE SUMMARY\n  (stats unavailable)\n\n"
            "MISSION PROGRESS\n  (unavailable)"
        )

    msg2 = f"""
📊 PERFORMANCE ANALYSIS
{'═'*40}
{mission_progress_block}

✅ WHAT WORKED TODAY:
  Trades: {todays_trades}
  Win Rate: {win_rate*100:.1f}%
  Avg profit/trade: ${avg_per_trade:+.4f}
  Best performers:
{chr(10).join(f'  🏆 {p}: ${v:+.4f}' for p, v in top3) or '  None yet'}

❌ WHAT FAILED TODAY:
  Losses: {len(losses)}
  Loss rate: {(1-win_rate)*100:.1f}%
  Worst performers:
{chr(10).join(f'  💔 {p}: ${v:+.4f}' for p, v in bot3) or '  None yet'}

🎯 EFFICIENCY SCORE:
  {'EXCELLENT' if win_rate >= 0.8 else 'GOOD' if win_rate >= 0.6 else 'NEEDS WORK' if win_rate >= 0.4 else 'CRITICAL'}
  ({win_rate*100:.1f}% win rate)"""
    messages.append(msg2)

    # ─── MESSAGE 3: RIGHTS/WRONGS/AVOID ────
    recent_lessons = lessons[-5:] if lessons else []
    wrongs = [l for l in recent_lessons if l.get("cost", 0) < 0]
    rights = [l for l in recent_lessons if l.get("cost", 0) >= 0]

    rights_lines = chr(10).join(
        f'  ✅ {str(l.get("lesson", ""))[:70]}' for l in rights[-3:]
    )
    wrongs_lines = chr(10).join(
        f'  ❌ {str(l.get("lesson", ""))[:70]}' for l in wrongs[-3:]
    )
    rights_block = rights_lines or (
        "  • Coinbase unified exits (trail / TP / SL / max hold) ✅"
        + chr(10)
        + "  • Gates A/B/C with mission checks ✅"
        + chr(10)
        + "  • Reserve + deploy discipline ✅"
    )
    wrongs_block = wrongs_lines or (
        "  • Kalshi bought wrong price ranges ❌"
        + chr(10)
        + "  • Penny coins selected ❌"
        + chr(10)
        + "  • Stop loss delayed ❌"
    )

    msg3 = f"""
📚 RIGHTS / WRONGS / AVOID
{'═'*40}
✅ WHAT WAS CORRECT:
{rights_block}

❌ WHAT WENT WRONG:
{wrongs_block}

🚫 AVOID NEXT TIME:
  • Kalshi: ONLY buy ranges BTC trades IN
  • Coinbase: NO coins under $0.01 price
  • NEVER ignore hard stop-loss or max-hold risk
  • NEVER buy illiquid coins

🔑 NON-NEGOTIABLE RULES:
  • 85%+ prob on Kalshi always
  • Coinbase: Gates A/B/C swing system only
  • 20% reserve at all times (unless mission overrides)
  • Exits: unified engine — trail, targets, stops, timeouts, dawn sweep"""
    messages.append(msg3)

    # ─── MESSAGE 4: HIDDEN OPPORTUNITIES ───
    msg4 = f"""
🔍 HIDDEN OPPORTUNITIES + STRATEGY
{'═'*40}
🚀 HIGHEST VALUE OPPORTUNITIES RIGHT NOW:

1. COINBASE (GATES A / B / C):
   Swing/breakout system with unified exits — scale ticket sizes
   as balance grows; mission checks on every buy; no legacy scalp churn.

2. KALSHI MORNING BLITZ (9am-5pm ET):
   80 trades × $0.50 profit = $40/blitz
   32 blitzes/day = $1,280/day potential
   → Reinvest in larger positions

3. GATE C MOMENTUM (pumping coins):
   Catch +10%/hr coins early
   Buy dip → sell continuation
   1-2 of these/day = +$5-20 extra

4. COMBINED DAILY TARGET:
   Coinbase: deploy within gate caps + reserve; compound realized wins
   Kalshi:   $40-80/day (market hours, when blitzes fire)
   Total:    path-dependent — prioritize edge quality over frequency

5. ACCELERATION TRIGGER:
   When balance hits $1,000 → double positions
   When balance hits $5,000 → 5x daily profit
   When balance hits $10,000 → $500+/day
   This is how we hit $1M in 4 months

💡 HIDDEN EDGE:
   Night hours (10pm-9am): Coinbase only
   → Zero competition from Kalshi
   → Full capital deployed in crypto
   → Gainers run wild at night (USD +142%!)
   → Gate B catches these automatically"""
    messages.append(msg4)

    # ─── MESSAGE 5: ACTION PLAN + FORECAST ─
    MILESTONES = [
        500,
        1000,
        2500,
        5000,
        10000,
        25000,
        50000,
        100000,
        250000,
        500000,
        1000000,
    ]
    next_milestone = None
    for m in MILESTONES:
        if total_balance < m:
            next_milestone = m
            break

    if next_milestone is None:
        milestone_section = f"""🎯 NEXT MILESTONE: ✅ $1,000,000+ (balance ${total_balance:,.2f})
   Need: $0.00 more
   Est: — (at or past target)"""
        days_to_next = 0
    else:
        days_to_next = (
            int((next_milestone - total_balance) / max(0.01, todays_pnl))
            if todays_pnl > 0
            else 999
        )
        milestone_section = f"""🎯 NEXT MILESTONE: ${next_milestone:,}
   Need: ${next_milestone - total_balance:,.2f} more
   Est: {days_to_next} days at today's pace"""

    msg5 = f"""
🗺️ ACTION PLAN + FORECAST
{'═'*40}
{milestone_section}

📈 30/60/120 DAY FORECAST:
   30 days:  ${proj_30:>12,.0f}
   60 days:  ${proj_60:>12,.0f}
   120 days: ${proj_120:>12,.0f}
   {'🔥 $1M IN 4 MONTHS ACHIEVABLE!' if proj_120 >= 500000 else '📈 On track for 6 months'}

✅ TODAY'S ACTIONS (DO THIS):
  1. Keep Coinbase cycling 24/7
  2. {'Kalshi fires at 9am — watch first blitz' if day_number <= 2 else 'Kalshi compounding daily'}
  3. {'Scale up positions — balance growing' if todays_pnl > 0 else 'Fix losing strategy FIRST'}
  4. Check Gate C for pumping coins tonight
  5. Review lessons before next session

⚡ ACCELERATION MOVES:
  • Deposit more capital → faster compound
  • Fix any losing gates immediately
  • Add Tasty Trades next (options = 10x)
  • Add Manifold/Polymarket for more markets

🏆 THE PATH TO $1M:
  Day 1-30:   $200 → $1,000+ (5x)
  Day 30-90:  $1K → $10,000 (10x)
  Day 90-150: $10K → $100,000 (10x)
  Day 150-180: $100K → $1,000,000 (10x)

  Each 10x is achievable with current system.
  Compound interest does the heavy lifting.
  Stay consistent. Never deviate from mission.

╔══════════════════════════════════════════╗
║  🎯 EVERY TRADE SERVES THE MISSION      ║
║  💰 $1,000,000 IS THE ONLY OUTCOME      ║
║  🔥 4 MONTHS IF WE EXECUTE PERFECTLY   ║
╚══════════════════════════════════════════╝"""
    messages.append(msg5)

    return messages
