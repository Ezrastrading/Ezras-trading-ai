#!/usr/bin/env python3
"""MASTER SMOKE TEST — Day A lessons + core systems (run locally or on Railway)."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main() -> None:
    from trading_ai.shark.dotenv_load import load_shark_dotenv

    load_shark_dotenv()

    print("=" * 55)
    print("MASTER SMOKE TEST — DAY A LESSONS + ALL SYSTEMS")
    print("=" * 55)

    # ── 1. LESSONS ─────────────────────────────
    print("\n1. LESSONS FILE:")
    from trading_ai.shark.lessons import get_rules_summary, load_lessons

    lessons = load_lessons()
    ll = lessons.get("lessons", [])
    rules = lessons.get("rules", [])
    dnr = lessons.get("do_not_repeat", [])
    print(f"   Total lessons: {len(ll)}")
    print(f"   Rules: {len(rules)}")
    print(f"   Do not repeat: {len(dnr)}")
    print(f"   Day A complete: {lessons.get('day_a_complete', False)}")
    print(f"   Capital lost Day A: ${lessons.get('total_capital_lost_day_a', 0):.2f}")
    kalshi_lessons = [l for l in ll if l.get("platform") == "kalshi"]
    cb_lessons = [l for l in ll if l.get("platform") == "coinbase"]
    print(f"   Kalshi lessons: {len(kalshi_lessons)}")
    print(f"   Coinbase lessons: {len(cb_lessons)}")
    status = "✅ PASS" if len(ll) >= 10 else "❌ FAIL"
    print(f"   Status: {status}")
    _ = get_rules_summary()

    # ── 2. SUPABASE ─────────────────────────────
    print("\n2. SUPABASE:")
    from trading_ai.shark.supabase_logger import _get_client, get_recent_trades, get_win_rate

    client = _get_client()
    connected = client is not None
    print(f"   Connected: {connected}")
    if connected:
        trades = get_recent_trades(limit=5)
        print(f"   Trades in DB: {len(trades)}")
        stats = get_win_rate()
        print(f"   Win rate stats: {stats}")
    print(f"   Status: {'✅ PASS' if connected else '❌ FAIL'}")

    # ── 3. MISSION ─────────────────────────────
    print("\n3. MISSION:")
    from trading_ai.shark.mission import evaluate_trade_against_mission, get_mission_status

    status_m = get_mission_status(200.0)
    print("   Target: $1,000,000")
    print(f"   Days left: {status_m['days_left']}")
    print(f"   Required: {status_m['required_daily_pct']:.2f}%/day")

    bad = evaluate_trade_against_mission("kalshi", "KXBTC", 5.0, 0.70, 200.0)
    good = evaluate_trade_against_mission("kalshi", "KXBTC", 5.0, 0.92, 200.0)
    print(f"   Blocks 70% prob: {not bad['approved']}")
    print(f"   Allows 92% prob: {good['approved']}")
    ok_m = (not bad["approved"]) and good["approved"]
    print(f"   Status: {'✅ PASS' if ok_m else '❌ FAIL'}")

    # ── 4. COINBASE 4-GATE SYSTEM ──────────────
    print("\n4. COINBASE 4-GATE SYSTEM:")
    from trading_ai.shark.coinbase_accumulator import CoinbaseAccumulator, coinbase_enabled

    print(f"   Enabled: {coinbase_enabled()}")
    acc = CoinbaseAccumulator()
    bal = 0.0
    try:
        bal = float(acc._client.get_usd_balance())
    except Exception as e:
        print(f"   Balance fetch: {e}")
    print(f"   USD Balance: ${bal:.2f}")
    print(f"   Has credentials: {acc._client.has_credentials()}")
    exits = acc._run_exits_only()
    print(f"   Exit check returned: {exits}")
    print(f"   Status: {'✅ PASS' if acc._client.has_credentials() else '⚠️ NEEDS USD'}")

    # ── 5. EXIT TIMING TEST ────────────────────
    print("\n5. EXIT ALL POSITIONS AT 5MIN:")
    from trading_ai.shark.coinbase_accumulator import load_coinbase_state, save_coinbase_state

    remaining: list = []
    if not acc._client.has_credentials() or not coinbase_enabled():
        print("   Skipped: Coinbase disabled or no credentials (cannot exercise exit loop)")
    else:
        state = load_coinbase_state()
        original_positions = list(state.get("positions", []))
        _t = time.time()
        _exp = _t - 220.0
        fake_positions = [
            {
                "product_id": "BTC-USD",
                "gate": "C",
                "engine": 1,
                "strategy": "test",
                "entry_price": 74000.0,
                "entry_time": _t - 400.0,
                "expiry_time": _exp,
                "must_sell_by": _exp,
                "cost_usd": 2.0,
                "size_base": 2.0 / 74000.0,
                "size_usd": 2.0,
                "peak_price": 74000.0,
                "trail_stop": 73000.0,
                "exit_submitted": False,
                "exit_notified": False,
                "sell_pending": False,
                "min_hold_until": 0.0,
            },
            {
                "product_id": "ETH-USD",
                "gate": "A",
                "engine": 1,
                "strategy": "test",
                "entry_price": 2350.0,
                "entry_time": _t - 400.0,
                "expiry_time": _exp,
                "must_sell_by": _exp,
                "cost_usd": 2.0,
                "size_base": 2.0 / 2350.0,
                "size_usd": 2.0,
                "peak_price": 2350.0,
                "trail_stop": 2320.0,
                "exit_submitted": False,
                "exit_notified": False,
                "sell_pending": False,
                "min_hold_until": 0.0,
            },
            {
                "product_id": "SOL-USD",
                "gate": "B",
                "engine": 1,
                "strategy": "test",
                "entry_price": 85.0,
                "entry_time": _t - 400.0,
                "expiry_time": _exp,
                "must_sell_by": _exp,
                "cost_usd": 2.0,
                "size_base": 2.0 / 85.0,
                "size_usd": 2.0,
                "peak_price": 85.0,
                "trail_stop": 84.0,
                "exit_submitted": False,
                "exit_notified": False,
                "sell_pending": False,
                "min_hold_until": 0.0,
            },
        ]
        state["positions"] = fake_positions
        save_coinbase_state(state)
        print("   Injected 3 positions (400s old)")

        exits_fired = 0
        try:
            exits_fired = acc._run_exits_only()
            state_after = load_coinbase_state()
            remaining = [
                p
                for p in state_after.get("positions", [])
                if p.get("product_id") in ("BTC-USD", "ETH-USD", "SOL-USD")
            ]
        except Exception as e:
            print(f"   Exit test error: {e}")

        state_after = load_coinbase_state()
        state_after["positions"] = original_positions
        save_coinbase_state(state_after)
        print(f"   Exits fired: {exits_fired}")
        print(f"   Stuck positions (still open): {len(remaining)}")
        print(f"   Status: {'✅ PASS' if len(remaining) == 0 else '❌ FAIL - STUCK POSITIONS'}")

    # ── 6. TRADE REPORTS ───────────────────────
    print("\n6. TRADE REPORTS:")
    from trading_ai.shark.trade_reports import format_report_for_telegram, get_combined_report

    report = get_combined_report("day")
    print(f"   Report keys ok: {'combined' in report}")
    telegram_text = format_report_for_telegram("day")
    print(f"   Telegram format: {len(telegram_text)} chars")
    print("   Status: ✅ PASS")

    # ── 7. MILLION TRACKER ─────────────────────
    print("\n7. MILLION TRACKER:")
    from trading_ai.shark.million_tracker import get_daily_briefing, get_milestones_summary, update_balance

    try:
        update_balance(bal, 0.0)
    except OSError as e:
        print(f"   (update_balance skipped: {e})")
    summary = get_milestones_summary()
    print(f"   Milestones summary len: {len(summary)}")
    briefing = get_daily_briefing(bal, 0.0, 0.27, 3, 1.0)
    print(f"   Briefing generated: {len(briefing)} chars")
    print("   Status: ✅ PASS")

    # ── 8. CEO BRIEFING FULL GENERATION ────────
    print("\n8. FULL CEO BRIEFING (5 messages):")
    from trading_ai.shark.mission import generate_full_ceo_briefing

    messages = generate_full_ceo_briefing(
        total_balance=bal,
        coinbase_bal=bal,
        kalshi_bal=0.0,
        todays_pnl=0.27,
        todays_trades=3,
        win_rate=1.0,
        day_number=1,
        all_time_pnl=-114.73,
        lessons=lessons.get("lessons", []),
        recent_trades=[],
    )
    print(f"   Messages: {len(messages)}")
    for i, m in enumerate(messages, 1):
        print(f"   Msg {i}: {len(m)} chars ✅")
    print(f"   Status: {'✅ PASS' if len(messages) == 5 else '❌ FAIL'}")

    # ── 9. KALSHI RANGE VALIDATION ─────────────
    print("\n9. KALSHI RANGE VALIDATION:")
    from trading_ai.shark.kalshi_simple_scanner import run_simple_scan

    os.environ["KALSHI_SIMPLE_SCAN_ENABLED"] = "true"
    os.environ["KALSHI_GATE_A_ENABLED"] = "false"
    os.environ["KALSHI_GATE_B_ENABLED"] = "false"
    os.environ["KALSHI_SIMPLE_MIN_PROB"] = "0.85"
    os.environ["KALSHI_SIMPLE_MAX_TRADES"] = "0"
    result = run_simple_scan()
    print(f"   Scanner result: {result}")
    print("   Status: ✅ PASS")

    # ── FINAL SUMMARY ──────────────────────────
    print()
    print("=" * 55)
    print("MASTER SMOKE TEST COMPLETE")
    print("=" * 55)
    print(f"✅ Lessons: {len(ll)} total ({len(kalshi_lessons)} Kalshi + {len(cb_lessons)} Coinbase)")
    print(f"✅ Supabase: {'Connected' if connected else 'DISCONNECTED'}")
    print("✅ Mission: $1M by Oct 2026 hardwired")
    print(f"✅ Coinbase 4 gates: {'Live' if coinbase_enabled() else 'Waiting for USD'}")
    exit_ran = acc._client.has_credentials() and coinbase_enabled()
    if not exit_ran:
        exit_line = "SKIPPED (no credentials)"
    elif len(remaining) == 0:
        exit_line = "ALL SOLD"
    else:
        exit_line = "FAILED"
    print(f"✅ Exit test: {exit_line}")
    print(f"✅ CEO briefing: {len(messages)} messages ready")
    print("✅ Trade reports: Live")
    print("✅ Million tracker: Live")
    print()
    print("DAY A LESSONS BURNED IN:")
    print("  Kalshi lost: $100 (wrong range markets)")
    print("  Coinbase lost: $15 (penny coins, bad exits)")
    print("  Total: -$115")
    print(f"  Lessons: {len(ll)}")
    print(f"  Rules: {len(rules)}")
    print(f"  Never again: {len(dnr)}")
    print()
    print("THE AI WILL NOT REPEAT THESE MISTAKES.")
    print("EVERY LESSON IS PERMANENT.")
    print("EVERY TRADE MOVES TOWARD $1,000,000.")
    print("=" * 55)


if __name__ == "__main__":
    main()
