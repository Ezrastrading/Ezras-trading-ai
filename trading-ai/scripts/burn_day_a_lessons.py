#!/usr/bin/env python3
"""One-shot: merge Day A lessons and write trading-ai/shark/state/lessons.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "shark" / "state" / "lessons.json"


def main() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from trading_ai.shark.lessons import DEFAULT_LESSONS, load_lessons, save_lessons

    lessons = load_lessons()

    kalshi_lessons = [
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "kalshi",
            "category": "market_selection",
            "severity": "CRITICAL",
            "cost": -100.00,
            "lesson": (
                "FATAL: Bot bought BTC price RANGE markets ($73,500-$73,599, $75,700-$75,799 etc) "
                "when BTC was at $74,984. These ranges NEVER resolved YES because BTC was not trading "
                "IN those ranges at expiry. RULE: Before buying ANY Kalshi range market, check: is "
                "current_price >= range_low AND current_price <= range_high? If NO → SKIP. Always."
            ),
            "what_went_wrong": (
                "Simple scanner selected markets by probability only (89-94%) without checking if "
                "current BTC price was inside the range. A 94% probability means nothing if BTC is "
                "$1000 away from the range."
            ),
            "what_to_do": (
                "Add price_in_range() check to kalshi_simple_scanner.py. For KXBTCD markets: parse "
                "range from ticker, fetch current BTC price, only buy if price is within range ± $500 buffer."
            ),
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "kalshi",
            "category": "market_hours",
            "severity": "HIGH",
            "cost": -50.00,
            "lesson": (
                "Kalshi BTC/ETH markets ONLY exist 9am-5pm ET. Trading these at midnight finds stale "
                "markets with wrong probabilities. After 5pm ET: DISABLE crypto blitz entirely. Only "
                "trade non-time-sensitive markets at night."
            ),
            "what_went_wrong": (
                "Bot tried to trade BTC/ETH Kalshi markets at 11pm when they were closed or showing wrong prices."
            ),
            "what_to_do": (
                "Add market_hours_check() — if current time is outside 9am-5pm ET, skip ALL "
                "KXBTCD/KXBTC/KXETHD/KXETH markets."
            ),
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "kalshi",
            "category": "position_sizing",
            "severity": "HIGH",
            "cost": -30.00,
            "lesson": (
                "Bot deployed entire $100 Kalshi balance into wrong markets in one session. Never deploy "
                "more than 20% of Kalshi balance per scan. $100 balance = max $20 per scan. Preserve "
                "capital to fight another day."
            ),
            "what_went_wrong": "No per-scan capital limit. All $100 deployed at once into bad trades.",
            "what_to_do": "KALSHI_SIMPLE_MAX_ORDER_USD = min(10.00, balance * 0.20 / max_trades). Hard limit.",
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "kalshi",
            "category": "scanner_logic",
            "severity": "HIGH",
            "cost": -20.00,
            "lesson": (
                "Scanner selected markets based on probability alone. 94% on a range market where BTC "
                "is $1000 away is NOT a 94% win. The probability shown is the market's consensus — not "
                "whether BTC will be in THAT range. Always validate the underlying condition independently."
            ),
            "what_went_wrong": (
                "Blindly trusting Kalshi probability without independent validation of the underlying condition."
            ),
            "what_to_do": (
                "For BTC/ETH range markets: fetch real BTC price from exchange API, validate independently. "
                "Trust your own calculation over market consensus."
            ),
            "applied": True,
        },
    ]

    coinbase_lessons = [
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "coinbase",
            "category": "coin_selection",
            "severity": "CRITICAL",
            "cost": -15.00,
            "lesson": (
                "Gate B bought penny coins: $0.0001637, $0.0001940, $0.0000003 price. These have ZERO "
                "liquidity. Cannot sell them. They sit in portfolio forever losing value. ABSOLUTE RULE: "
                "Never buy any coin with price < $0.01 OR 24h volume < $500K. No exceptions ever."
            ),
            "what_went_wrong": (
                "Gate B scanned all 385 products without minimum price/volume filter. Gainer detection "
                "found coins up 50% but they were penny coins up from $0.0001 to $0.0002."
            ),
            "what_to_do": (
                "COINBASE_MIN_PRODUCT_PRICE=0.01, COINBASE_MIN_VOLUME_USD=500000 enforced in buy preflight. "
                "Already fixed in code. Verify it works."
            ),
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "coinbase",
            "category": "exit_execution",
            "severity": "CRITICAL",
            "cost": -10.00,
            "lesson": (
                "Positions held 20+ minutes past 5-minute time stop. SOL, BTC, AVAX, DOT, UNI all bought "
                "9:21-9:44pm still open at 10:13pm. The time stop was coded but not firing on all positions "
                "— only 1-2 per scan instead of all. Root cause: exit loop broke after first sell and saved "
                "state incorrectly."
            ),
            "what_went_wrong": (
                "exit loop used positions[i+1:] slice but broke early. Only first position was getting sold each scan cycle."
            ),
            "what_to_do": (
                "Use snapshot pattern: positions_snapshot = list(state['positions']). Process ALL. Already "
                "fixed with remaining + snapshot[idx+1:] pattern. Verify ALL positions sell at 5min."
            ),
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "coinbase",
            "category": "stop_loss",
            "severity": "CRITICAL",
            "cost": -8.00,
            "lesson": (
                "Stop loss not firing when price fetch returns 0 for illiquid coins. Positions showing "
                "-$0.05, -$0.03, -$0.04 losses not being sold. When price = 0 → treat as stop loss, sell "
                "immediately. Never hold a position with no price data."
            ),
            "what_went_wrong": "Old code: if no price → keep position (wrong). Should be: if no price → emergency sell.",
            "what_to_do": "no_price_stop exit reason added. If bid=0 or no price → sell immediately. Already in code. Verify fires.",
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "coinbase",
            "category": "profit_capture",
            "severity": "HIGH",
            "cost": 0,
            "lesson": (
                "MISSED OPPORTUNITY: USD coin up +142%, SUP +63%, XAN +27%, APR +46% at night. Gate B was "
                "not scanning properly for these massive gainers. Night trading is the BEST time for altcoin "
                "pumps — retail traders drive wild moves. Gate C must catch these."
            ),
            "what_went_wrong": "Gate C not enabled or not finding high-momentum coins. Missed $100+ profit opportunities.",
            "what_to_do": (
                "Enable Gate C (COINBASE_GATE_C_ENABLED=true). Lower gainer threshold to 5% in 1hr. "
                "Ensure night scanning active. These are the real money makers."
            ),
            "applied": False,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "coinbase",
            "category": "gate_a_signals",
            "severity": "MEDIUM",
            "cost": -5.00,
            "lesson": (
                "Gate A barely fired because 0.2% dip threshold too high for overnight BTC/ETH which barely "
                "moved. Lowered to 0.1% dip and 0.05% momentum. Gate A must maintain 10 minimum positions "
                "at ALL times — even if signals are weak."
            ),
            "what_went_wrong": "Gate A sat at 0-3 positions for hours because threshold too strict.",
            "what_to_do": (
                "COINBASE_GATE_A_DIP_PCT=0.001, COINBASE_GATE_A_MOM_PCT=0.0005. Gate A urgent mode: when < "
                "10 positions, bypass signal filter and buy best available."
            ),
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "coinbase",
            "category": "profit_target",
            "severity": "MEDIUM",
            "cost": -3.00,
            "lesson": (
                "Profit target 0.5% too high for scalping. Delta-neutral default: 0.15% or $0.15 min per trade, "
                "0.12% max loss, 3 min time stop — compound hundreds of micro wins."
            ),
            "what_went_wrong": "Original 0.5% profit target meant positions rarely hit target in the time window.",
            "what_to_do": "COINBASE_PROFIT_TARGET_PCT=0.0015, COINBASE_MIN_PROFIT_USD=0.15. Already set. Verify cycling.",
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day_A",
            "platform": "both",
            "category": "duplicate_notifications",
            "severity": "LOW",
            "cost": 0,
            "lesson": (
                "Same trade sent 3x Telegram notifications. Confusing and hides real signals. "
                "exit_notified=True flag prevents duplicate sends. One notification per exit event only."
            ),
            "what_went_wrong": "exit_notified flag not checked before sending.",
            "what_to_do": "Already fixed. Always check exit_notified before send_telegram.",
            "applied": True,
        },
    ]

    all_new = kalshi_lessons + coinbase_lessons
    existing_lessons = list(lessons.get("lessons") or [])
    existing_sessions = {str(l.get("lesson", ""))[:50] for l in existing_lessons}

    for le in all_new:
        if str(le["lesson"])[:50] not in existing_sessions:
            existing_lessons.append(le)
            existing_sessions.add(str(le["lesson"])[:50])

    lessons["version"] = lessons.get("version") or DEFAULT_LESSONS.get("version") or 1
    lessons["lessons"] = existing_lessons
    lessons["last_updated"] = "2026-04-16"
    lessons["day_a_complete"] = True
    lessons["total_capital_lost_day_a"] = -115.00
    lessons["day_a_summary"] = (
        "Day A: Lost $100 Kalshi (wrong range markets), "
        "$15 Coinbase (penny coins, missed stops). "
        "Total: -$115. Key lessons: validate range "
        "conditions independently, filter penny coins, "
        "fix exit loop, add no-price stop, catch night "
        "gainers. All critical fixes applied."
    )
    lessons["rules"] = [
        "KALSHI: Only buy ranges where current price IS inside the range",
        "KALSHI: Validate independently — don't trust probability alone",
        "KALSHI: Only trade 9am-5pm ET for BTC/ETH markets",
        "KALSHI: Max 20% of balance per scan, never all-in",
        "KALSHI: Min 85% probability required",
        "COINBASE: Gate A = BTC/ETH/SOL/XRP/DOGE only",
        "COINBASE: Min price $0.01, min volume $500K — ABSOLUTE",
        "COINBASE: ALL positions exit at 5min — no exceptions",
        "COINBASE: Stop loss fires even when price = 0",
        "COINBASE: Exit loop processes ALL positions per scan",
        "COINBASE: Profit scan every 3s — sell at first profit",
        "COINBASE: 20% reserve always kept, never touch",
        "COINBASE: Gate C for night gainers — enable always",
        "GENERAL: Never deploy more than 20% per scan",
        "GENERAL: Capital preservation > profit chasing",
        "GENERAL: Compound every profit immediately",
        "GENERAL: Learn from every loss — log everything",
    ]
    lessons["do_not_repeat"] = [
        "Buying Kalshi BTC range markets where BTC is NOT in range",
        "Buying Kalshi markets after 5pm ET",
        "Deploying entire balance in one session",
        "Trusting probability without validating condition",
        "Buying coins under $0.01 price",
        "Buying coins under $500K daily volume",
        "Holding past 5 minutes — hard stop no exceptions",
        "Ignoring stop loss when price = 0",
        "Only processing 1-2 exits per scan cycle",
        "Setting profit target too high for position size",
        "Missing night altcoin pumps (+100%+ opportunities)",
        "Sending duplicate Telegram notifications",
        "Going all-in on unvalidated signals",
        "Buying illiquid coins that cannot be sold",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(lessons, f, indent=2)

    save_lessons(lessons)
    print("Day A lessons saved:", len(lessons["lessons"]), "total")
    print("Rules:", len(lessons["rules"]))
    print("Do not repeat:", len(lessons["do_not_repeat"]))
    print("Written:", OUT)


if __name__ == "__main__":
    main()
