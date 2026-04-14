"""CLI: ``python -m trading_ai post-trade …``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from trading_ai.automation.post_trade_hub import execute_post_trade_closed, execute_post_trade_placed


def _read_json(path: Optional[Path], use_stdin: bool) -> dict:
    if use_stdin:
        raw = sys.stdin.read()
    elif path is not None:
        raw = path.read_text(encoding="utf-8")
    else:
        raise SystemExit("need --json-file or --stdin")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("JSON root must be an object")
    return data


def main_post_trade(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="trading-ai post-trade")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("placed", help="Run placed trigger from JSON trade object")
    pl.add_argument("--json-file", "-f", type=Path)
    pl.add_argument("--stdin", action="store_true")

    cl = sub.add_parser("closed", help="Run closed trigger from JSON trade object")
    cl.add_argument("--json-file", "-f", type=Path)
    cl.add_argument("--stdin", action="store_true")

    ff = sub.add_parser(
        "from-file",
        help="JSON must include event or event_type: placed | closed",
    )
    ff.add_argument("--json-file", "-f", type=Path, required=True)

    sub.add_parser("simulate-placed", help="Minimal sample placed payload")
    sub.add_parser("simulate-closed", help="Minimal sample closed payload")

    args = p.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "placed":
        payload = _read_json(args.json_file, args.stdin)
        out = execute_post_trade_placed(None, payload)
        print(json.dumps(out, indent=2, default=str))
        if out.get("status") in ("sent", "skipped_duplicate", "processed_partial"):
            try:
                from trading_ai.ops.automation_heartbeat import record_heartbeat

                record_heartbeat("post_trade", ok=True, note="placed")
            except Exception:
                pass
            return 0
        return 1

    if args.cmd == "closed":
        payload = _read_json(args.json_file, args.stdin)
        out = execute_post_trade_closed(None, payload)
        print(json.dumps(out, indent=2, default=str))
        if out.get("status") in ("sent", "skipped_duplicate", "processed_partial"):
            try:
                from trading_ai.ops.automation_heartbeat import record_heartbeat

                record_heartbeat("post_trade", ok=True, note="closed")
            except Exception:
                pass
            return 0
        return 1

    if args.cmd == "from-file":
        data = json.loads(args.json_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("JSON root must be an object")
        ev = str(data.get("event") or data.get("event_type") or "").strip().lower()
        if ev not in ("placed", "closed"):
            print(
                json.dumps({"ok": False, "error": "JSON must include event or event_type: placed|closed"}),
                file=sys.stderr,
            )
            return 2
        if ev == "placed":
            out = execute_post_trade_placed(None, data)
        else:
            out = execute_post_trade_closed(None, data)
        print(json.dumps(out, indent=2, default=str))
        if out.get("status") in ("sent", "skipped_duplicate", "processed_partial"):
            try:
                from trading_ai.ops.automation_heartbeat import record_heartbeat

                record_heartbeat("post_trade", ok=True, note=f"from-file:{ev}")
            except Exception:
                pass
        return 0

    if args.cmd == "simulate-placed":
        fake = {
            "trade_id": "sim-post-trade-placed",
            "timestamp": "2026-04-13T12:00:00+00:00",
            "market": "SIM",
            "position": "YES",
            "entry_price": 0.45,
            "capital_allocated": 80.0,
            "signal_score": 7,
            "expected_value": 0.03,
            "event_name": "sim",
        }
        out = execute_post_trade_placed(None, fake)
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.cmd == "simulate-closed":
        fake = {
            "trade_id": "sim-post-trade-closed",
            "timestamp": "2026-04-13T14:00:00+00:00",
            "market": "SIM",
            "position": "YES",
            "exit_price": 0.99,
            "result": "win",
            "roi_percent": 10.0,
            "capital_allocated": 80.0,
            "gross_pnl_dollars": 8.0,
            "net_pnl_dollars": 7.5,
            "total_execution_cost_dollars": 0.5,
            "event_name": "sim",
        }
        out = execute_post_trade_closed(None, fake)
        print(json.dumps(out, indent=2, default=str))
        return 0

    return 2
