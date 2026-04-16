"""
Lessons learned from every trading session.
AI reads this before making any trade decision.
Updated after every CEO briefing automatically.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

LESSONS_FILE = shark_state_path("lessons.json")


def _repo_committed_lessons_path() -> Optional[Path]:
    """``trading-ai/shark/state/lessons.json`` — Day A seed when runtime file is absent."""
    try:
        shark_dir = Path(__file__).resolve().parent
        root = shark_dir.parent.parent.parent
        p = root / "shark" / "state" / "lessons.json"
        if p.is_file():
            return p
    except Exception:
        pass
    return None

DEFAULT_LESSONS: Dict[str, Any] = {
    "version": 1,
    "last_updated": None,
    "lessons": [
        {
            "date": "2026-04-16",
            "session": "Day A",
            "platform": "kalshi",
            "lesson": (
                "NEVER buy price RANGE markets unless BTC is currently trading IN that range. "
                "Bot bought $73,500-$73,599 range when BTC was at $74,984. Always check: is "
                "current_price WITHIN the range? If not, skip."
            ),
            "cost": -100.00,
            "category": "market_selection",
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day A",
            "platform": "coinbase",
            "lesson": (
                "Never buy coins with price < $0.01 or volume < $500K daily. Penny coins like "
                "$0.0001637 have no liquidity and cannot be sold. Always filter by min price "
                "$0.01 and min volume $500K."
            ),
            "cost": -15.00,
            "category": "coin_selection",
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day A",
            "platform": "coinbase",
            "lesson": (
                "Stop loss must check price even when API returns 0. If price fetch fails for a "
                "position, treat as stop loss immediately. Never hold a position with no price data."
            ),
            "cost": -8.00,
            "category": "risk_management",
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day A",
            "platform": "coinbase",
            "lesson": (
                "Exit loop must process ALL positions per scan. Original code broke after first "
                "sell. New code uses snapshot list so all positions are evaluated every 3-5 seconds."
            ),
            "cost": -5.00,
            "category": "execution",
            "applied": True,
        },
        {
            "date": "2026-04-16",
            "session": "Day A",
            "platform": "both",
            "lesson": (
                "Duplicate Telegram notifications waste attention. Every position needs "
                "exit_notified=True flag. Only send one notification per exit event."
            ),
            "cost": 0,
            "category": "notifications",
            "applied": True,
        },
    ],
    "rules": [
        "KALSHI: Only buy ranges where current price is INSIDE the range",
        "KALSHI: Min probability 85% on YES or NO side",
        "KALSHI: Max TTR 3600 seconds (1 hour)",
        "COINBASE: Gate A = BTC/ETH/SOL/XRP/DOGE only",
        "COINBASE: Min coin price $0.01",
        "COINBASE: Min 24h volume $500K",
        "COINBASE: Stop loss fires even when price = 0",
        "COINBASE: Exit ALL positions at 5min no exceptions",
        "COINBASE: Never buy same coin twice in same gate",
        "GENERAL: % based sizing only, never fixed $",
        "GENERAL: 20% reserve always kept",
        "GENERAL: Profit scan every 3s",
        "GENERAL: Exit check every 5s",
        "GENERAL: Buy scan every 30s",
    ],
    "do_not_repeat": [
        "Buying Kalshi price ranges where BTC is NOT trading",
        "Buying penny coins under $0.01",
        "Holding positions past 5 minutes",
        "Firing stop loss only at 5min instead of immediately",
        "Sending duplicate exit notifications",
        "Using fixed $ amounts instead of %",
        "Buying illiquid coins that cannot be sold",
    ],
}


def load_lessons() -> dict:
    path = LESSONS_FILE
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        logger.warning("load_lessons: falling back to defaults", exc_info=True)
    try:
        rp = _repo_committed_lessons_path()
        if rp is not None:
            with open(rp, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        logger.warning("load_lessons: repo lessons.json unreadable", exc_info=True)
    return dict(DEFAULT_LESSONS)


def save_lessons(lessons: dict) -> None:
    try:
        path = LESSONS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lessons, f, indent=2)
        tmp.replace(path)
    except Exception as e:
        logger.warning("save_lessons failed: %s", e)


def add_lesson(
    platform: str,
    lesson: str,
    cost: float,
    category: str,
    session: Optional[str] = None,
) -> None:
    lessons = load_lessons()
    lessons["lessons"].append(
        {
            "date": str(date.today()),
            "session": session or "auto",
            "platform": platform,
            "lesson": lesson,
            "cost": cost,
            "category": category,
            "applied": False,
        }
    )
    lessons["last_updated"] = str(date.today())
    save_lessons(lessons)
    try:
        from trading_ai.shark.supabase_logger import _get_client, log_ai_insight

        log_ai_insight(
            insight_type="lesson",
            platform=platform,
            gate="system",
            observation=lesson,
            recommendation=f"Category: {category}",
        )
        client = _get_client()
        if client:
            client.table("lessons").insert(
                {
                    "session": session or "auto",
                    "platform": platform,
                    "lesson": lesson,
                    "cost": cost,
                    "category": category,
                    "applied": False,
                }
            ).execute()
    except Exception:
        pass


def get_rules_summary() -> str:
    lessons = load_lessons()
    rules = lessons.get("rules", [])
    dont = lessons.get("do_not_repeat", [])
    return (
        "TRADING RULES:\n"
        + "\n".join(f"• {r}" for r in rules)
        + "\n\nNEVER DO AGAIN:\n"
        + "\n".join(f"✗ {d}" for d in dont)
    )
