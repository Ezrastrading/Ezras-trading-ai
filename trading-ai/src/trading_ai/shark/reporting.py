"""Telegram + memos — real-time alerts. No time-window blocking."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests as _requests

from trading_ai.config import Settings, get_settings
from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)


def trading_capital_usd_for_alerts(*, fallback: float) -> float:
    """Kalshi USD from treasury (live balance sync); else ``fallback`` (e.g. capital.json)."""
    try:
        from trading_ai.shark.treasury import load_treasury

        t = load_treasury()
        return float(t.get("kalshi_balance_usd", fallback) or 0.0)
    except Exception:
        return fallback


def require_telegram_credentials() -> tuple[str, str]:
    """Raise `EnvironmentError` if Telegram env is incomplete (for explicit checks / tests)."""
    from trading_ai.shark.required_env import require_telegram_credentials as _req

    return _req()

_LAST_ALERTS: List[Dict[str, Any]] = []


def log_telegram_failure(message: str, err: str) -> None:
    logger.error("telegram failure: %s | %s", err, message[:200])


def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = _requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False


def send_telegram_live(message: str) -> bool:
    """Alias for live path."""
    return send_telegram(message)


def send_setup_ping() -> bool:
    return send_telegram("🦈 Ezras setup test — system initializing")


def send_margin_trade_alert(
    *,
    intent: Any,
    deposited_capital: float,
    confidence: float,
) -> bool:
    """Telegram when a fill uses borrowed notional above cash capital."""
    mb = float(intent.meta.get("margin_borrowed", 0.0))
    cap_pct = float(intent.meta.get("margin_cap_pct", 0.0)) * 100.0
    text = (
        "⚠️ MARGIN TRADE\n"
        f" Position: ${float(intent.notional_usd):.2f}\n"
        f" Deposited capital: ${deposited_capital:.2f}\n"
        f" Borrowed: ${mb:.2f}\n"
        f" Margin used: {(mb / max(deposited_capital, 1e-9)) * 100:.1f}%\n"
        f" Confidence: {confidence:.2f}\n"
        f" Max allowed: {cap_pct:.1f}%"
    )
    return send_telegram(text)


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
    claude_reasoning: Optional[str] = None,
    claude_confidence: Optional[float] = None,
) -> str:
    body = (
        "⚡ TRADE FIRED\n"
        f"Hunt: {hunt} | Tier: {tier}\n"
        f"Outlet: {outlet}\n"
        f"Position: ${position_dollars:.2f} | Edge: {edge_pct*100:.2f}%\n"
        f"Market: {market_desc}\n"
        f"Resolves: {resolves_in}"
    )
    if claude_reasoning:
        cc = float(claude_confidence) if claude_confidence is not None else None
        conf_line = f"\nConfidence: {cc*100:.0f}%" if cc is not None else ""
        body += f"\nClaude: {claude_reasoning}{conf_line}"
    return body


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


def send_loss_postmortem_alert(postmortem: Dict[str, Any], analysis: Dict[str, Any]) -> bool:
    """Telegram summary after loss post-mortem + Claude adaptation (HTML-safe)."""
    import html

    src = str(postmortem.get("source") or "")
    is_journal = src == "trade_journal_all_venues"
    mana = float(postmortem.get("total_mana_lost", 0) or 0)
    n = int(postmortem.get("total_losses", 0) or 0)
    root = html.escape(str(analysis.get("root_cause", ""))[:500])
    patterns = analysis.get("patterns") or []
    if not isinstance(patterns, list):
        patterns = []
    pat_lines = "\n".join(f"- {html.escape(str(p)[:200])}" for p in patterns[:6]) or "- (none listed)"
    recovery = html.escape(str(analysis.get("recovery_strategy", ""))[:500])
    pc = analysis.get("parameter_changes") or {}
    if not isinstance(pc, dict):
        pc = {}
    hunts = pc.get("hunt_type_to_disable") or []
    if not isinstance(hunts, list):
        hunts = []
    edge_adj = pc.get("min_edge_adjustment") or {}
    if not isinstance(edge_adj, dict):
        edge_adj = {}
    edge_lines = "\n".join(
        f"- {html.escape(str(k))}: {html.escape(str(v))}" for k, v in list(edge_adj.items())[:8]
    )
    if not edge_lines:
        edge_lines = "- (none)"
    hunts_txt = ", ".join(html.escape(str(h)) for h in hunts) if hunts else "(none)"
    header = "📉 FULL JOURNAL LOSS ANALYSIS" if is_journal else "📉 MANA LOSS ANALYSIS"
    lost_line = (
        f" Lost (abs P/L, mixed notionals): ${mana:.2f}\n" if is_journal else f" Lost: {mana:.0f} mana\n"
    )
    body = (
        f"{header}\n"
        f"{lost_line}"
        f" Trades: {n} losing trades\n\n"
        "🤖 Claude Analysis:\n"
        f"Root cause: {root}\n\n"
        "Key patterns:\n"
        f"{pat_lines}\n\n"
        "Recovery strategy:\n"
        f"{recovery}\n\n"
        "Changes applied:\n"
        f"- Hunts disabled: {hunts_txt}\n"
        f"- Edge adjustments:\n{edge_lines}\n\n"
        "System is adapting. 🦈"
    )
    _remember("journal_loss_postmortem" if is_journal else "mana_loss_postmortem", {"text": body})
    return bool(send_telegram(body))


def send_excel_report(file_path: Path, date_str: str, stats: Dict[str, Any]) -> bool:
    """Send daily Excel workbook via Telegram ``sendDocument``."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return False
    p = Path(file_path)
    if not p.is_file():
        logger.warning("excel report file missing: %s", p)
        return False
    wr = float(stats.get("win_rate", 0) or 0)
    tp = float(stats.get("total_pnl", 0) or 0)
    nt = int(stats.get("total_trades", 0) or 0)
    caption = (
        f"📊 Daily Trading Report — {date_str}\n\n"
        f"Trades: {nt}\n"
        f"Win Rate: {wr:.1%}\n"
        f"Total P/L: ${tp:+.2f}\n\n"
        "Full breakdown in Excel ↑"
    )
    try:
        with p.open("rb") as f:
            resp = _requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={
                    "document": (
                        p.name,
                        f,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                timeout=120,
            )
        ok = resp.status_code == 200
        if not ok:
            logger.warning("sendDocument failed: %s %s", resp.status_code, resp.text[:300])
        _remember("daily_excel_report", {"date": date_str, "path": str(p), "ok": ok})
        return ok
    except Exception as exc:
        logger.warning("send_excel_report failed: %s", exc)
        return False


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
    claude_reasoning: Optional[str] = None,
    claude_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    text = format_trade_fired(
        hunt=",".join(hunt_types),
        tier=tier,
        outlet=outlet or "n/a",
        position_dollars=capital * position_fraction,
        edge_pct=edge,
        market_desc=market_desc or "market",
        resolves_in=resolves_in or "TBD",
        claude_reasoning=claude_reasoning,
        claude_confidence=claude_confidence,
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


def _best_outlet_label_for_memo(outlet_scores: Dict[str, float]) -> str:
    """Highest Bayesian outlet excluding Manifold unless ``MANIFOLD_REAL_MONEY=true``."""
    if not outlet_scores:
        return "n/a"
    rm = (os.environ.get("MANIFOLD_REAL_MONEY") or "").strip().lower() == "true"
    ranked = sorted(outlet_scores.items(), key=lambda x: x[1], reverse=True)
    for name, _ in ranked:
        if name.lower() == "manifold" and not rm:
            continue
        return name
    return "n/a"


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
    best_out = _best_outlet_label_for_memo(outlet_scores)
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
    kalshi_usd: float,
    win_rate: float,
    best_hunt: str,
    trades_today: int,
    gaps_monitored: List[str],
) -> str:
    return (
        "DAILY SHARK SUMMARY (08:00)\n"
        f"Capital: ${kalshi_usd:.2f}\n"
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


def format_weekly_mana_section() -> str:
    """CLI / operator diagnostics only — never sent via Telegram."""
    from trading_ai.shark.mana_sandbox import get_mana_summary, top_mana_strategy

    s = get_mana_summary()
    bal = float(s.get("mana_balance", 0) or 0)
    n = int(s.get("total_mana_trades", 0) or 0)
    wr = s.get("mana_win_rate")
    wr_pct = f"{float(wr) * 100:.1f}%" if wr is not None else "n/a"
    top = top_mana_strategy(dict(s.get("strategy_performance") or {}))
    return (
        "📊 MANA SANDBOX (Learning Mode)\n"
        f" Mana balance: {bal:.2f}\n"
        f" Mana trades: {n}\n"
        f" Mana win rate: {wr_pct}\n"
        f" Top strategy: {top}\n"
        " Insights applied to real trades: ✅"
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
    """Scheduled heartbeat (e.g. every 6h) — Telegram summary."""
    from trading_ai.shark.state_store import load_capital

    uptime_h = (time.time() - started_at) / 3600.0
    rec = load_capital()
    cap_usd = trading_capital_usd_for_alerts(fallback=rec.current_capital)
    total = max(rec.total_trades, 1)
    wr = (rec.winning_trades / total) if rec.total_trades else None
    server = "railway" if (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip() else "local"
    text = format_shark_heartbeat_message(
        uptime_hours=uptime_h,
        capital=cap_usd,
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
    """Banner: active trading capital is Kalshi USD; optional Manifold USD when ``MANIFOLD_REAL_MONEY``."""
    _ = gaps_n
    k = trading_capital_usd_for_alerts(fallback=capital)
    extra = ""
    rm = (os.environ.get("MANIFOLD_REAL_MONEY") or "").strip().lower() == "true"
    if rm:
        try:
            from trading_ai.shark.treasury import load_treasury

            musd = float(load_treasury().get("manifold_usd_balance", 0.0) or 0.0)
            if musd > 0:
                extra = f" Manifold (USD): ${musd:.2f}\n"
        except Exception:
            pass
    return (
        "🦈 Ezras Shark System — LIVE\n"
        f" Capital: ${k:.2f} (Kalshi)\n"
        + extra
        + f" Phase: {phase}\n"
        " Scanning: Kalshi | Mode: 24/7\n"
        " Targets: MINIMUM expectations.\n"
        " Faster is always better. 🦈\n"
        " System is hunting. Always."
    )
