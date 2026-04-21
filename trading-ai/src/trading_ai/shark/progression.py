"""
Tracks every trade and generates hourly/daily/
weekly/monthly summaries for CEO briefings.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.shark.lessons import load_lessons

logger = logging.getLogger(__name__)

PROGRESSION_FILE = shark_state_path("progression.json")
_lock = threading.Lock()


def _default_progression() -> Dict[str, Any]:
    return {
        "all_trades": [],
        "total_trades": 0,
        "total_pnl": 0.0,
        "total_wins": 0,
        "total_losses": 0,
        "peak_balance": 0.0,
        "start_balance": None,
    }


def _load_progression() -> Dict[str, Any]:
    p = PROGRESSION_FILE
    try:
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base = _default_progression()
                for k in base:
                    if k in raw:
                        base[k] = raw[k]
                if not isinstance(base.get("all_trades"), list):
                    base["all_trades"] = []
                return base
    except Exception:
        logger.warning("load_progression: using defaults", exc_info=True)
    return _default_progression()


def _save_progression(prog: Dict[str, Any]) -> None:
    p = PROGRESSION_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prog, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def record_trade(
    platform: str,
    gate: str,
    product_id: str,
    pnl_usd: float,
    exit_reason: str,
    hold_seconds: int,
    balance_after: float,
    win: bool,
) -> None:
    with _lock:
        prog = _load_progression()
        now = time.time()

        trade = {
            "ts": now,
            "platform": platform,
            "gate": gate,
            "product_id": product_id,
            "pnl_usd": pnl_usd,
            "exit_reason": exit_reason,
            "hold_seconds": hold_seconds,
            "balance": balance_after,
            "win": win,
        }

        prog["all_trades"].append(trade)
        prog["total_trades"] += 1
        prog["total_pnl"] += pnl_usd
        if win:
            prog["total_wins"] += 1
        else:
            prog["total_losses"] += 1

        if balance_after > prog.get("peak_balance", 0):
            prog["peak_balance"] = balance_after

        if not prog.get("start_balance"):
            prog["start_balance"] = balance_after

        _save_progression(prog)

    try:
        from trading_ai.shark.supabase_logger import _get_client

        client = _get_client()
        if client:
            client.table("progression").insert(
                {
                    "platform": platform,
                    "gate": gate,
                    "product_id": product_id,
                    "pnl_usd": pnl_usd,
                    "exit_reason": exit_reason,
                    "hold_seconds": hold_seconds,
                    "balance_after": balance_after,
                    "win": win,
                }
            ).execute()
    except Exception:
        pass


def get_summary(period: str = "today") -> dict:
    with _lock:
        prog = _load_progression()
    now = time.time()

    if period == "today":
        cutoff = now - 86400
    elif period == "week":
        cutoff = now - 604800
    elif period == "month":
        cutoff = now - 2592000
    else:
        cutoff = 0.0

    trades: List[Dict[str, Any]] = [t for t in prog["all_trades"] if t["ts"] >= cutoff]

    if not trades:
        return {"period": period, "trades": 0}

    wins = sum(1 for t in trades if t["win"])
    pnl = sum(t["pnl_usd"] for t in trades)

    return {
        "period": period,
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": wins / len(trades),
        "pnl_usd": pnl,
        "avg_pnl": pnl / len(trades),
        "best_trade": max(trades, key=lambda x: x["pnl_usd"]),
        "worst_trade": min(trades, key=lambda x: x["pnl_usd"]),
        "start_balance": prog.get("start_balance"),
        "current_balance": trades[-1]["balance"],
        "peak_balance": prog.get("peak_balance"),
    }


def generate_ceo_briefing() -> str:
    today = get_summary("today")
    week = get_summary("week")
    with _lock:
        prog = _load_progression()
    lessons = load_lessons()

    wr_today = float(today.get("win_rate") or 0.0)
    wr_week = float(week.get("win_rate") or 0.0)

    current_bal = float(today.get("current_balance") or 0.0)
    if int(today.get("trades") or 0) == 0:
        all_t = prog.get("all_trades") or []
        if all_t:
            current_bal = float(all_t[-1].get("balance") or 0.0)
        elif prog.get("start_balance") is not None:
            current_bal = float(prog["start_balance"])

    return f"""
🏦 CEO BRIEFING — {time.strftime('%Y-%m-%d %H:%M')}
{'=' * 50}

📊 TODAY:
  Trades: {today.get('trades', 0)}
  Wins: {today.get('wins', 0)} | Losses: {today.get('losses', 0)}
  Win Rate: {wr_today * 100:.1f}%
  P&L: ${float(today.get('pnl_usd', 0) or 0):+.4f}
  Avg/trade: ${float(today.get('avg_pnl', 0) or 0):+.4f}

📈 THIS WEEK:
  Trades: {week.get('trades', 0)}
  P&L: ${float(week.get('pnl_usd', 0) or 0):+.4f}
  Win Rate: {wr_week * 100:.1f}%

💰 BALANCE:
  Start: ${float(prog.get('start_balance') or 0):.2f}
  Current: ${current_bal:.2f}
  Peak: ${float(prog.get('peak_balance') or 0):.2f}
  All-time P&L: ${float(prog.get('total_pnl', 0) or 0):+.4f}

📚 ACTIVE LESSONS: {len(lessons.get('lessons', []))}
🚫 DO NOT REPEAT: {len(lessons.get('do_not_repeat', []))}

{'=' * 50}
"""
