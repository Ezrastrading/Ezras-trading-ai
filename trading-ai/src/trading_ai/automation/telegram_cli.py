"""CLI: ``python -m trading_ai telegram …`` and ``vault-cycle …``."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Optional, Sequence

from trading_ai.config import get_settings

from trading_ai.automation.telegram_ops import send_telegram_with_idempotency
from trading_ai.automation.telegram_trade_events import (
    format_trade_closed_message,
    format_trade_placed_message,
)
from trading_ai.automation.vault_cycle_summaries import (
    build_evening_vault_summary,
    build_morning_vault_summary,
    format_evening_telegram,
    format_morning_telegram,
    record_morning_snapshot,
)


def main_telegram(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="trading-ai telegram")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="Send a minimal test message (uses .env Telegram credentials)")

    p_sim_p = sub.add_parser("simulate-placed", help="Send a sample TRADE OPEN alert (dedupe key is synthetic)")
    p_sim_p.add_argument(
        "--trade-id",
        default="sim-placed",
        help="trade_id for the simulated alert (default: sim-placed)",
    )
    p_sim_p.add_argument(
        "--force",
        action="store_true",
        help="Send even if this trade_id was already used (no dedupe)",
    )

    p_sim_c = sub.add_parser("simulate-closed", help="Send a sample TRADE CLOSED alert")
    p_sim_c.add_argument(
        "--trade-id",
        default="sim-closed",
        help="trade_id for the simulated alert",
    )
    p_sim_c.add_argument("--force", action="store_true", help="Send even if already sent for this trade_id")

    args = p.parse_args(list(argv) if argv is not None else None)
    settings = get_settings()

    if args.cmd == "ping":
        r = send_telegram_with_idempotency(
            settings,
            "Ezras Trading AI — Telegram ping (trading-ai telegram ping)",
            dedupe_key=None,
            event_label="telegram_ping",
        )
        print(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1

    if args.cmd == "simulate-placed":
        fake = {
            "trade_id": args.trade_id,
            "timestamp": "2026-01-01T12:00:00+00:00",
            "market": "SIM-MARKET",
            "position": "YES",
            "entry_price": 0.42,
            "capital_allocated": 100.0,
            "signal_score": 8,
            "expected_value": 0.04,
            "event_name": "simulated",
        }
        text = format_trade_placed_message(fake)
        dk = None if args.force else f"placed:{args.trade_id}"
        r = send_telegram_with_idempotency(
            settings,
            text,
            dedupe_key=dk,
            event_label="simulate_placed",
        )
        print(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1

    if args.cmd == "simulate-closed":
        fake = {
            "trade_id": args.trade_id,
            "timestamp": "2026-01-02T12:00:00+00:00",
            "market": "SIM-MARKET",
            "position": "YES",
            "exit_price": 0.99,
            "result": "win",
            "roi_percent": 12.5,
            "capital_allocated": 100.0,
            "gross_pnl_dollars": 12.5,
            "net_pnl_dollars": 12.0,
            "total_execution_cost_dollars": 0.5,
            "event_name": "simulated",
        }
        text = format_trade_closed_message(fake)
        dk = None if args.force else f"closed:{args.trade_id}"
        r = send_telegram_with_idempotency(
            settings,
            text,
            dedupe_key=dk,
            event_label="simulate_closed",
        )
        print(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1

    return 2


def main_vault_cycle(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="trading-ai vault-cycle")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("morning", help="Build morning summary, optional Telegram")
    m.add_argument("--send", action="store_true", help="Send Telegram (non-blocking failure)")

    e = sub.add_parser("evening", help="Build evening summary, optional Telegram")
    e.add_argument("--send", action="store_true")

    args = p.parse_args(list(argv) if argv is not None else None)
    settings = get_settings()

    if args.cmd == "morning":
        summ = build_morning_vault_summary()
        snap = record_morning_snapshot()
        summ["morning_snapshot"] = snap
        print(json.dumps(summ, indent=2))
        if args.send:
            text = format_morning_telegram(summ)
            day = datetime.now(timezone.utc).date().isoformat()
            r = send_telegram_with_idempotency(
                settings,
                text,
                dedupe_key=f"vault_morning:{day}",
                event_label="vault_morning",
            )
            print(json.dumps({"telegram": r}, indent=2))
        try:
            from trading_ai.ops.automation_heartbeat import record_heartbeat

            record_heartbeat("morning_cycle", ok=True, note="vault-cycle morning")
        except Exception:
            pass
        return 0

    if args.cmd == "evening":
        summ = build_evening_vault_summary()
        print(json.dumps(summ, indent=2))
        if args.send:
            text = format_evening_telegram(summ)
            day = datetime.now(timezone.utc).date().isoformat()
            r = send_telegram_with_idempotency(
                settings,
                text,
                dedupe_key=f"vault_evening:{day}",
                event_label="vault_evening",
            )
            print(json.dumps({"telegram": r}, indent=2))
        try:
            from trading_ai.ops.automation_heartbeat import record_heartbeat

            record_heartbeat("evening_cycle", ok=True, note="vault-cycle evening")
        except Exception:
            pass
        return 0

    return 2
