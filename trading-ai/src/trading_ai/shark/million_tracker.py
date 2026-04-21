"""
Tracks progress toward $1,000,000 in 6-8 months.
Claude reads this daily to assess trajectory.
Generates 1-day briefing 3-5 messages direct.
Identifies hidden opportunities and improvements.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

MILLION_FILE = shark_state_path("million_tracker.json")

TARGET = 1_000_000.00
START_DATE = "2026-04-16"
TARGET_DATE_FAST = "2026-10-16"  # 6 months
TARGET_DATE_SLOW = "2026-12-16"  # 8 months


def _load() -> dict:
    try:
        p = Path(MILLION_FILE)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {
        "start_date": START_DATE,
        "start_balance": 200.00,
        "target": TARGET,
        "snapshots": [],
        "milestones": [
            {"amount": 500, "hit": False, "date": None},
            {"amount": 1000, "hit": False, "date": None},
            {"amount": 2500, "hit": False, "date": None},
            {"amount": 5000, "hit": False, "date": None},
            {"amount": 10000, "hit": False, "date": None},
            {"amount": 25000, "hit": False, "date": None},
            {"amount": 50000, "hit": False, "date": None},
            {"amount": 100000, "hit": False, "date": None},
            {"amount": 250000, "hit": False, "date": None},
            {"amount": 500000, "hit": False, "date": None},
            {"amount": 1000000, "hit": False, "date": None},
        ],
        "daily_growth_needed": 0.0,
        "notes": [],
    }


def update_balance(coinbase_balance: float, kalshi_balance: float) -> None:
    data = _load()
    total = coinbase_balance + kalshi_balance
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    snapshot = {
        "date": now_str,
        "unix_ts": time.time(),
        "coinbase": coinbase_balance,
        "kalshi": kalshi_balance,
        "total": total,
    }
    data["snapshots"].append(snapshot)

    for m in data["milestones"]:
        if not m["hit"] and total >= m["amount"]:
            m["hit"] = True
            m["date"] = now_str
            logger.info("🎯 MILESTONE HIT: $%s on %s", m["amount"], now_str)

    start = data.get("start_balance", 200)
    if start > 0 and total > 0:
        days_left = 180  # 6 months target
        if total < TARGET:
            needed = (TARGET / total) ** (1 / days_left) - 1
            data["daily_growth_needed"] = needed

    p = Path(MILLION_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def get_daily_briefing(
    coinbase_bal: float,
    kalshi_bal: float,
    todays_pnl: float,
    todays_trades: int,
    win_rate: float,
) -> str:
    data = _load()
    total = coinbase_bal + kalshi_bal
    start = data.get("start_balance", 200)

    pct_to_million = (total / TARGET) * 100
    growth_from_start = ((total - start) / start * 100) if start > 0 else 0

    start_ts = datetime.strptime(START_DATE, "%Y-%m-%d").timestamp()
    days_elapsed = (time.time() - start_ts) / 86400

    days_left = max(1, 180 - days_elapsed)
    required_daily = (
        ((TARGET / total) ** (1 / days_left) - 1) * 100 if total > 0 else 0
    )

    snapshots = data.get("snapshots", [])
    recent = [s for s in snapshots if s["unix_ts"] > time.time() - 604800]
    if len(recent) >= 2 and recent[0].get("total", 0) > 0:
        actual_daily = (
            (recent[-1]["total"] / recent[0]["total"]) ** (1 / max(1, len(recent) - 1)) - 1
        ) * 100
    else:
        actual_daily = 0

    next_milestone = None
    for m in data["milestones"]:
        if not m["hit"]:
            next_milestone = m
            break

    on_track = actual_daily >= required_daily * 0.8
    track_emoji = "✅" if on_track else "⚠️"

    if next_milestone is None:
        msg4 = f"""
MESSAGE 4 — NEXT MILESTONE:
🎯 All milestones reached — target ${TARGET:,.0f}"""
    else:
        nm_amt = float(next_milestone["amount"])
        need_more = nm_amt - total
        est_days = (
            int(need_more / max(0.01, todays_pnl)) if todays_pnl > 0 else "?"
        )
        msg4 = f"""
MESSAGE 4 — NEXT MILESTONE:
🎯 Target: ${nm_amt:,.0f}
💰 Need: ${need_more:,.2f} more
📅 Est. days: {est_days}"""

    briefing = f"""
🎯 DAILY BRIEFING — PATH TO $1,000,000
{'═'*40}

MESSAGE 1 — WHERE WE ARE:
💰 Total Balance: ${total:,.2f}
📈 From Start ($200): +{growth_from_start:.1f}%
🎯 To Million: {pct_to_million:.2f}% there
📅 Day {int(days_elapsed)} of 180

MESSAGE 2 — TODAY'S PERFORMANCE:
📊 Trades: {todays_trades}
💵 P&L: ${todays_pnl:+.4f}
🏆 Win Rate: {win_rate*100:.1f}%
🟡 Coinbase: ${coinbase_bal:.2f}
🔴 Kalshi: ${kalshi_bal:.2f}

MESSAGE 3 — TRAJECTORY:
{track_emoji} On Track: {'YES' if on_track else 'FALLING BEHIND'}
📉 Required daily growth: {required_daily:.2f}%
📈 Actual daily growth: {actual_daily:.2f}%
{'✅ AHEAD OF SCHEDULE' if actual_daily > required_daily else '⚠️ NEED TO ACCELERATE'}
{msg4}

MESSAGE 5 — ACTION PLAN:
{'🚀 Keep current pace — on track for 6 months' if on_track else '⚡ ACCELERATE: Increase position sizes or find better markets'}
• Coinbase: {'✅ Running' if coinbase_bal > 0 else '❌ Check'}
• Kalshi: {'✅ Ready for 9am' if kalshi_bal > 0 else '⚠️ Needs funding'}
• Next step: {'Deploy more capital' if total < 500 else 'Let compound interest work'}
{'═'*40}"""

    return briefing


def get_milestones_summary() -> str:
    data = _load()
    lines = ["🏆 MILESTONE TRACKER:"]
    for m in data["milestones"]:
        if m["hit"]:
            lines.append(f"  ✅ ${m['amount']:,} — {m['date']}")
        else:
            lines.append(f"  ⬜ ${m['amount']:,}")
    return "\n".join(lines)
