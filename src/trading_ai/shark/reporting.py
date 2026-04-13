"""Telegram + memos — real-time alerts. No time-window blocking."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.config import Settings, get_settings
from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)


def require_telegram_credentials() -> tuple[str, str]:
    """Raise `EnvironmentError` if Telegram env is incomplete (for explicit checks / tests)."""
    from trading_ai.shark.required_env import require_telegram_credentials as _req

    return _req()

_LAST_ALERTS: List[Dict[str, Any]] = []

_TG_QUEUE: "queue.Queue[str]" = queue.Queue()
_TG_WORKER_STARTED = False
_TG_LOCK = threading.Lock()
_LAST_TG_SEND = 0.0
_TG_MIN_INTERVAL = 3.0


def log_telegram_failure(message: str, err: str) -> None:
    logger.error("telegram failure: %s | %s", err, message[:200])


def _tg_post_once(text: str) -> bool:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return False
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed; pip install requests")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log_telegram_failure(text, f"HTTP {r.status_code}: {r.text[:200]}")
            time.sleep(5)
            r2 = requests.post(url, json=payload, timeout=10)
            if r2.status_code != 200:
                log_telegram_failure(text, f"retry HTTP {r2.status_code}")
                return False
        return True
    except Exception as exc:
        log_telegram_failure(text, str(exc))
        try:
            time.sleep(5)
            r3 = requests.post(url, json=payload, timeout=10)
            return r3.status_code == 200
        except Exception as exc2:
            log_telegram_failure(text, str(exc2))
            return False


def _tg_worker() -> None:
    global _LAST_TG_SEND
    while True:
        text = _TG_QUEUE.get()
        if text is None:
            _TG_QUEUE.task_done()
            break
        with _TG_LOCK:
            now = time.time()
            wait = _TG_MIN_INTERVAL - (now - _LAST_TG_SEND)
            if wait > 0:
                time.sleep(wait)
            _LAST_TG_SEND = time.time()
        _tg_post_once(text)
        _TG_QUEUE.task_done()


def _ensure_tg_worker() -> None:
    global _TG_WORKER_STARTED
    if _TG_WORKER_STARTED:
        return
    with _TG_LOCK:
        if _TG_WORKER_STARTED:
            return
        t = threading.Thread(target=_tg_worker, name="telegram-sender", daemon=True)
        t.start()
        _TG_WORKER_STARTED = True


def send_telegram(message: str) -> bool:
    """
    Queue message (rate-limited 1/3s). Retries once on failure inside _tg_post_once.
    Never raises.
    """
    _ensure_tg_worker()
    _TG_QUEUE.put(message)
    return True


def send_telegram_live(message: str) -> bool:
    """Alias for live path; returns True when queued (delivery async)."""
    return send_telegram(message)


def send_setup_ping() -> bool:
    return send_telegram("🦈 Ezras setup test — system initializing")


def _remember(kind: str, payload: Dict[str, Any]) -> None:
    _LAST_ALERTS.append({"kind": kind, "ts": datetime.now(timezone.utc).isoformat(), **payload})
    if len(_LAST_ALERTS) > 500:
        del _LAST_ALERTS[:200]


def last_alerts_for_tests() -> List[Dict[str, Any]]:
    return list(_LAST_ALERTS)


def clear_test_alerts() -> None:
    _LAST_ALERTS.clear()


def send_telegram_text(settings: Optional[Settings], text: str, *, dedupe_key: str, event_label: str) -> Dict[str, Any]:
    _ = dedupe_key, event_label
    if (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() and (os.environ.get("TELEGRAM_CHAT_ID") or "").strip():
        ok = send_telegram(text)
        return {"sent": ok, "skipped_duplicate": False, "ok": True, "error": None}
    from trading_ai.automation.telegram_ops import send_telegram_with_idempotency

    s = settings or get_settings()
    return send_telegram_with_idempotency(s, text, dedupe_key=dedupe_key, event_label=event_label)


def format_gap_detection_alert(
    *,
    gap_type: str,
    score: float,
    edge: float,
    volume: float,
    window_duration: str,
    recommended_allocation: float,
) -> str:
    return (
        "🦈 STRUCTURAL GAP DETECTED\n"
        f"Type: {gap_type}\n"
        f"Score: {score:.4f}\n"
        f"Edge per trade: {edge:.4f}\n"
        f"Volume available: {volume:.2f}\n"
        f"Estimated window: {window_duration}\n"
        f"Recommended allocation: ${recommended_allocation:.2f}\n"
        "Action: GAP EXPLOITATION MODE ACTIVE"
    )


def format_trade_fired(
    *,
    hunt: str,
    tier: str,
    outlet: str,
    position_dollars: float,
    edge_pct: float,
    market_desc: str,
    resolves_in: str,
) -> str:
    return (
        "⚡ TRADE FIRED\n"
        f"Hunt: {hunt} | Tier: {tier}\n"
        f"Outlet: {outlet}\n"
        f"Position: ${position_dollars:.2f} | Edge: {edge_pct*100:.2f}%\n"
        f"Market: {market_desc}\n"
        f"Resolves: {resolves_in}"
    )


def format_win_resolved(*, pnl: float, ret_pct: float, capital: float, day_pnl: float) -> str:
    return (
        "✅ WIN\n"
        f"P&L: +${pnl:.2f} | Return: {ret_pct*100:.2f}%\n"
        f"Capital: ${capital:.2f}\n"
        f"Running total today: +${day_pnl:.2f}"
    )


def format_loss_resolved(*, pnl: float, capital: float, cluster_status: str) -> str:
    return (
        "❌ LOSS\n"
        f"P&L: -${abs(pnl):.2f}\n"
        f"Capital: ${capital:.2f}\n"
        f"Cluster check: {cluster_status}"
    )


def format_gap_closed(*, duration: str, total_captured: float) -> str:
    return (
        "🔒 GAP CLOSED\n"
        f"Duration: {duration}\n"
        f"Total captured: ${total_captured:.2f}\n"
        "Returning to standard mode"
    )


def format_drawdown_alert(*, drawdown_pct: float, action: str) -> str:
    return (
        "⚠️ DRAWDOWN ALERT\n"
        f"Current: -{drawdown_pct*100:.1f}% from peak\n"
        f"Action: {action}"
    )


def alert_trade_fired(
    *,
    hunt_types: List[str],
    edge: float,
    position_fraction: float,
    capital: float,
    settings: Optional[Settings] = None,
    tier: str = "B",
    outlet: str = "",
    market_desc: str = "",
    resolves_in: str = "",
) -> Dict[str, Any]:
    text = format_trade_fired(
        hunt=",".join(hunt_types),
        tier=tier,
        outlet=outlet or "n/a",
        position_dollars=capital * position_fraction,
        edge_pct=edge,
        market_desc=market_desc or "market",
        resolves_in=resolves_in or "TBD",
    )
    _remember("trade_fired", {"text": text})
    return send_telegram_text(settings, text, dedupe_key=f"shark:fire:{hash(text)%10**9}", event_label="shark_trade_fired")


def alert_gap_detected(
    *,
    gap_type: str,
    score: float,
    edge: float,
    volume: float,
    window_duration: str,
    recommended_allocation: float,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    body = format_gap_detection_alert(
        gap_type=gap_type,
        score=score,
        edge=edge,
        volume=volume,
        window_duration=window_duration,
        recommended_allocation=recommended_allocation,
    )
    _remember("gap_detected", {"text": body})
    return send_telegram_text(settings, body, dedupe_key=f"shark:gap:{gap_type}:{score:.4f}", event_label="shark_gap_detected")


def alert_gap_closure(*, reason: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    text = f"🔒 GAP CLOSURE\nReason: {reason}"
    _remember("gap_closure", {"text": text})
    return send_telegram_text(settings, text, dedupe_key=f"shark:gap_close:{reason}", event_label="shark_gap_closure")


@dataclass
class DailyMemo:
    hunt_type_notes: str
    outlet_notes: str
    gaps_observed: str
    focus_24h: str
    capital_phase: str
    monthly_progress: str = ""
    year_end_trajectory: str = ""
    pace_status: str = ""


def build_daily_decision_memo(
    *,
    hunt_leaderboard: Dict[str, float],
    outlet_scores: Dict[str, float],
    gaps: List[str],
    phase: str,
    current_capital: Optional[float] = None,
    monthly_target: Optional[float] = None,
    monthly_start_capital: Optional[float] = None,
    year_end_target: Optional[float] = None,
    month_index: int = 1,
) -> DailyMemo:
    from trading_ai.shark.capital_phase import (
        monthly_progress_ratio,
        year_end_pace_status,
        YEAR_END_TARGET_DEFAULT,
    )

    best_hunt = max(hunt_leaderboard, key=hunt_leaderboard.get) if hunt_leaderboard else "n/a"
    best_out = max(outlet_scores, key=outlet_scores.get) if outlet_scores else "n/a"
    mp = ""
    ye = ""
    pace = ""
    if current_capital is not None and monthly_target is not None and monthly_start_capital is not None:
        pr = monthly_progress_ratio(current_capital, monthly_target, monthly_start_capital)
        mp = f"Monthly target progress: {pr * 100:.1f}% (slot M{month_index})"
    yref = year_end_target if year_end_target is not None else YEAR_END_TARGET_DEFAULT
    if current_capital is not None:
        ye = f"Year-end trajectory vs ${yref:,.0f} target"
        pace = year_end_pace_status(current_capital, yref, float(month_index))
    return DailyMemo(
        hunt_type_notes=f"Best performing hunt type (Bayesian): {best_hunt}",
        outlet_notes=f"Best outlet (Bayesian): {best_out}",
        gaps_observed="; ".join(gaps) if gaps else "none under active observation",
        focus_24h=f"24/7 scan; no clock windows; phase {phase}",
        capital_phase=phase,
        monthly_progress=mp,
        year_end_trajectory=ye,
        pace_status=pace,
    )


def format_daily_summary(
    *,
    capital: float,
    win_rate: float,
    best_hunt: str,
    trades_today: int,
    gaps_monitored: List[str],
) -> str:
    return (
        "DAILY SHARK SUMMARY (08:00)\n"
        f"Capital: ${capital:.2f}\n"
        f"Win rate (7d proxy): {win_rate*100:.1f}%\n"
        f"Best hunt type: {best_hunt}\n"
        f"Trades fired: {trades_today}\n"
        f"Gaps monitored: {', '.join(gaps_monitored) or 'none'}"
    )


def format_weekly_summary(
    *,
    performance_lines: List[str],
    bayesian_snapshot: str,
    leaderboard: str,
) -> str:
    return (
        "WEEKLY SHARK REPORT\n"
        + "\n".join(performance_lines)
        + "\n\nBayesian weights:\n"
        + bayesian_snapshot
        + "\n\nStrategy leaderboard:\n"
        + leaderboard
    )


def format_shark_heartbeat_message(
    *,
    uptime_hours: float,
    capital: float,
    trades_today: int,
    win_rate_pct: Optional[float],
    server_label: str,
    next_scan_seconds: float,
) -> str:
    wr = f"{win_rate_pct * 100:.1f}%" if win_rate_pct is not None else "n/a"
    return (
        "🦈 SHARK ALIVE\n"
        f"Uptime: {uptime_hours:.1f}h\n"
        f"Capital: ${capital:.2f}\n"
        f"Trades today: {trades_today}\n"
        f"Win rate: {wr}\n"
        f"Server: {server_label}\n"
        f"Next scan: ~{int(next_scan_seconds)}s\n"
    )


def send_shark_heartbeat_alert(*, started_at: float) -> Dict[str, Any]:
    """Scheduled heartbeat (e.g. every 6h) — queue Telegram summary."""
    from trading_ai.shark.state_store import load_capital

    uptime_h = (time.time() - started_at) / 3600.0
    rec = load_capital()
    total = max(rec.total_trades, 1)
    wr = (rec.winning_trades / total) if rec.total_trades else None
    server = "railway" if (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip() else "local"
    text = format_shark_heartbeat_message(
        uptime_hours=uptime_h,
        capital=rec.current_capital,
        trades_today=rec.total_trades,
        win_rate_pct=wr,
        server_label=server,
        next_scan_seconds=300.0,
    )
    _remember("heartbeat", {"text": text})
    return send_telegram_text(
        None,
        text,
        dedupe_key=f"shark:heartbeat:{int(started_at // 21600)}",
        event_label="shark_heartbeat",
    )


def startup_banner(*, capital: float, phase: str, gaps_n: int) -> str:
    return (
        "🦈 Ezras Shark System — LIVE\n"
        f"Capital: ${capital:.2f} | Phase: {phase}\n"
        "Scanning: ALL OUTLETS | Mode: 24/7\n"
        f"Gaps monitored: {gaps_n}\n"
        "Targets: MINIMUM expectations. Faster is always better. 🦈\n"
        "System is hunting. Always."
    )
