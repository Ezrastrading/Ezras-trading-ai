"""Startup recovery: Supabase restore (handled in run_shark), positions, scan gap, Telegram."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def last_scan_path() -> Path:
    from trading_ai.governance.storage_architecture import shark_state_path

    return shark_state_path("last_scan.json")


def read_last_scan_unix() -> Optional[float]:
    p = last_scan_path()
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return float(raw.get("last_unix"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _open_position_from_dict(p: Dict[str, Any]) -> Any:
    from trading_ai.shark.models import OpenPosition

    return OpenPosition(
        position_id=str(p.get("position_id", "")),
        outlet=str(p.get("outlet", "")),
        market_id=str(p.get("market_id", "")),
        side=str(p.get("side", "yes")),
        entry_price=float(p.get("entry_price", 0.5)),
        shares=float(p.get("shares", 0.0)),
        notional_usd=float(p.get("notional_usd", 0.0)),
        order_id=str(p.get("order_id", "")),
        opened_at=float(p.get("opened_at", time.time())),
        strategy_key=str(p.get("strategy_key", "shark_default")),
        hunt_types=list(p.get("hunt_types") or []),
        market_category=str(p.get("market_category", "default")),
        expected_edge=float(p.get("expected_edge", 0.0)),
        condition_id=p.get("condition_id"),
        token_id=p.get("token_id"),
        margin_borrowed_usd=float(p.get("margin_borrowed_usd", 0.0)),
        claude_reasoning=p.get("claude_reasoning"),
        claude_confidence=p.get("claude_confidence"),
        claude_true_probability=p.get("claude_true_probability"),
        claude_decision=p.get("claude_decision"),
        journal_trade_id=str(p.get("journal_trade_id") or "") or None,
    )


def reconcile_open_positions() -> Dict[str, int]:
    """Best-effort: poll outlets for resolution; process closures when detected."""
    from trading_ai.shark.execution_live import calculate_pnl, handle_resolution, poll_resolution_for_outlet
    from trading_ai.shark.models import HuntType
    from trading_ai.shark.state_store import load_positions, save_positions

    data = load_positions()
    ops: List[Dict[str, Any]] = list(data.get("open_positions") or [])
    if not ops:
        return {"checked": 0, "resolved": 0}

    checked = 0
    resolved_n = 0
    remaining: List[Dict[str, Any]] = []
    for p in ops:
        checked += 1
        pos = _open_position_from_dict(p)
        try:
            out = poll_resolution_for_outlet(pos.outlet, pos.market_id, pos)
        except Exception as exc:
            logger.warning("poll resolution failed %s: %s", pos.market_id, exc)
            remaining.append(p)
            continue
        if out is None:
            remaining.append(p)
            continue
        try:
            pnl = calculate_pnl(pos, out)
            hts = pos.hunt_types or []
            hunt_enums: List[Any] = []
            for h in hts:
                try:
                    hunt_enums.append(HuntType(str(h)))
                except ValueError:
                    hunt_enums.append(HuntType.STRUCTURAL_ARBITRAGE)
            if not hunt_enums:
                hunt_enums = [HuntType.STRUCTURAL_ARBITRAGE]
            handle_resolution(
                pos,
                out,
                pnl,
                trade_id=pos.journal_trade_id or f"recovery-{pos.position_id}",
                strategy_key=pos.strategy_key,
                hunt_types=hunt_enums,
                market_category=pos.market_category,
            )
            resolved_n += 1
        except Exception as exc:
            logger.warning("handle_resolution failed %s: %s", pos.market_id, exc)
            remaining.append(p)

    if resolved_n:
        data["open_positions"] = remaining
        save_positions(data)
    return {"checked": checked, "resolved": resolved_n}


def run_startup_recovery(
    *,
    boot_unix: float,
    send_telegram: bool = True,
) -> Dict[str, Any]:
    """
    Run after state restore + integrity check.
    Returns a JSON-serializable report for logs.
    """
    from trading_ai.shark.reporting import trading_capital_usd_for_alerts
    from trading_ai.shark.state_store import load_capital, load_positions

    report: Dict[str, Any] = {
        "positions_checked": 0,
        "positions_resolved": 0,
        "last_scan_age_seconds": None,
        "restart_alert_sent": False,
        "offline_human": None,
    }

    pos_stats = reconcile_open_positions()
    report["positions_checked"] = pos_stats.get("checked", 0)
    report["positions_resolved"] = pos_stats.get("resolved", 0)

    now = time.time()
    last = read_last_scan_unix()
    rec = load_capital()
    cap_display = trading_capital_usd_for_alerts(fallback=rec.current_capital)
    pdata = load_positions()
    n_open = len((pdata.get("open_positions") or []))

    if last is not None:
        age = now - last
        report["last_scan_age_seconds"] = age
        if age > 600 and send_telegram:
            mins = int(age // 60)
            report["offline_human"] = f"{mins}m"
            try:
                from trading_ai.shark.reporting import send_telegram_live

                send_telegram_live(
                    "⚠️ SHARK RESTARTED\n"
                    f"Was offline (scan gap): ~{mins} min since last scan\n"
                    f"Open positions: {n_open}\n"
                    f"Capital: ${cap_display:.2f}\n"
                    "Resuming hunting now."
                )
                report["restart_alert_sent"] = True
            except Exception as exc:
                logger.warning("restart telegram failed: %s", exc)
    elif send_telegram and (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip():
        try:
            from trading_ai.shark.reporting import send_telegram_live

            send_telegram_live(
                "⚠️ SHARK RESTARTED\n"
                "No prior scan timestamp (cold start / new deploy).\n"
                f"Open positions: {n_open}\n"
                f"Capital: ${cap_display:.2f}\n"
                "Resuming hunting now."
            )
            report["restart_alert_sent"] = True
        except Exception as exc:
            logger.warning("restart telegram failed: %s", exc)

    report["boot_unix"] = boot_unix
    return report
