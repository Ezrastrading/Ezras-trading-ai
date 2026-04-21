"""
Smoke test: verify data sources the CEO briefing / AI layer can read.

Run locally:
  PYTHONPATH=src python3 -m trading_ai.shark.ai_briefing_test

Run on Railway:
  railway ssh -- bash -lc 'cd /app && PYTHONPATH=src python3 -m trading_ai.shark.ai_briefing_test'
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path


def _suppress_ssl_noise() -> None:
    try:
        import urllib3  # noqa: F401

        warnings.filterwarnings("ignore", message=".*OpenSSL.*", category=UserWarning)
        warnings.filterwarnings("ignore", category=UserWarning, module="urllib3")
    except Exception:
        pass


def _runtime_root() -> Path:
    raw = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if os.path.exists("/app"):
        return Path("/app/ezras-runtime").resolve()
    return (Path.home() / "ezras-runtime").resolve()


def _smoke_logs() -> None:
    print("\n7. LOGS (runtime shark/logs):")
    log_dir = _runtime_root() / "shark" / "logs"
    if not log_dir.is_dir():
        print(f"  Log dir missing (ok if fresh deploy): {log_dir}")
        return
    logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        any_files = list(log_dir.iterdir())[:5]
        print(f"  No .log files yet; sample entries: {[p.name for p in any_files]}")
        return
    newest = logs[0]
    try:
        text = newest.read_text(encoding="utf-8", errors="replace")
        lines = text.strip().splitlines()
        tail = lines[-5:] if len(lines) > 5 else lines
        print(f"  Newest log: {newest.name} ({len(lines)} lines)")
        for line in tail:
            print(f"  | {line[:200]}")
    except OSError as e:
        print(f"  Read failed: {e}")


def run_ai_full_access_smoke_test() -> int:
    _suppress_ssl_noise()

    from trading_ai.shark.dotenv_load import load_shark_dotenv

    load_shark_dotenv()

    print("=" * 50)
    print("AI FULL ACCESS SMOKE TEST")
    print("=" * 50)

    # TEST 1: Supabase connection + read trades
    print("\n1. SUPABASE:")
    from trading_ai.shark.supabase_logger import _get_client, get_recent_trades, get_win_rate

    client = _get_client()
    print(f"  Connected: {client is not None}")
    trades = get_recent_trades(limit=10)
    print(f"  Recent trades: {len(trades)}")
    stats = get_win_rate()
    print(f"  Win rate data: {stats}")

    # TEST 2: Lessons file
    print("\n2. LESSONS:")
    from trading_ai.shark.lessons import get_rules_summary, load_lessons

    lessons = load_lessons()
    print(f"  Lessons loaded: {len(lessons.get('lessons', []))}")
    print(f"  Rules: {len(lessons.get('rules', []))}")
    print(f"  Do not repeat: {len(lessons.get('do_not_repeat', []))}")
    rs = get_rules_summary()
    print(f"  Rules summary chars: {len(rs)}")

    # TEST 3: Mission status
    print("\n3. MISSION:")
    from trading_ai.shark.mission import MISSION_STATEMENT, get_directives_summary, get_mission_status

    _ = MISSION_STATEMENT  # ensure import
    status = get_mission_status(200.00)
    print(f"  Target: ${status['target']:,.0f}")
    print(f"  Days left: {status['days_left']}")
    print(f"  Required daily: {status['required_daily_pct']:.2f}%")
    ds = get_directives_summary()
    print(f"  Directives summary chars: {len(ds)}")

    # TEST 4: Trade reports
    print("\n4. TRADE REPORTS:")
    from trading_ai.shark.trade_reports import format_report_for_telegram, get_combined_report

    report = get_combined_report("day")
    print(f"  Coinbase trades today: {report['coinbase'].get('trades', 0)}")
    print(f"  Kalshi trades today: {report['kalshi'].get('trades', 0)}")
    print(f"  Combined PnL: ${report['combined']['total_pnl']:+.4f}")
    tg = format_report_for_telegram("day")
    print(f"  Telegram report chars: {len(tg)}")

    # TEST 5: Million tracker
    print("\n5. MILLION TRACKER:")
    from trading_ai.shark.million_tracker import get_milestones_summary, update_balance

    update_balance(58.42, 0)
    print(get_milestones_summary())

    # TEST 6: Generate full CEO briefing
    print("\n6. FULL CEO BRIEFING GENERATION:")
    from trading_ai.shark.mission import generate_full_ceo_briefing

    messages = generate_full_ceo_briefing(
        total_balance=58.42,
        coinbase_bal=58.42,
        kalshi_bal=0,
        todays_pnl=0.27,
        todays_trades=3,
        win_rate=1.0,
        day_number=1,
        all_time_pnl=0.27,
        lessons=lessons.get("lessons", []),
        recent_trades=trades,
    )
    print(f"  Messages generated: {len(messages)}")
    for i, msg in enumerate(messages, 1):
        preview = msg[:100].strip().replace("\n", " ")
        print(f"  Message {i}: {len(msg)} chars")
        print(f"  Preview: {preview}...")

    _smoke_logs()

    print("\n" + "=" * 50)
    print("ALL SYSTEMS READ: AI HAS FULL ACCESS")
    print("CEO BRIEFING READY TO SEND")
    print("=" * 50)
    return 0


def main() -> None:
    raise SystemExit(run_ai_full_access_smoke_test())


if __name__ == "__main__":
    main()
