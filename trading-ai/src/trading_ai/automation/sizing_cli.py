"""CLI: ``python -m trading_ai sizing …`` — inspect and simulate position sizing."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from trading_ai.automation.position_sizing_policy import (
    normalize_position_sizing_meta,
    simulate_sizing_cli,
    sizing_status_snapshot,
    validate_requested_size,
    validate_trade_open_invariants,
)


def main_sizing(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="trading-ai sizing", description="Account risk bucket position sizing (audit)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="JSON: raw vs effective bucket, policy, last decision")

    sub.add_parser(
        "validate-sample",
        help="Smoke: normalize + invariant check on a representative open payload; JSON to stdout; exit 1 if invariants fail",
    )

    sim = sub.add_parser("simulate", help="Apply policy to a size and bucket label (no side effects)")
    sim.add_argument("--size", type=float, required=True, help="Requested notional ($)")
    sim.add_argument(
        "--bucket",
        required=True,
        choices=("NORMAL", "REDUCED", "BLOCKED", "UNKNOWN"),
        help="Account risk bucket (UNKNOWN = failsafe → effective REDUCED)",
    )
    sim.add_argument(
        "--trade-id",
        default=None,
        help="Optional trade id for audit trails (echoed in JSON only)",
    )

    args = p.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "status":
        print(json.dumps(sizing_status_snapshot(), indent=2, default=str))
        return 0

    if args.cmd == "validate-sample":
        sample = {
            "trade_id": "cli-validate-sample",
            "timestamp": "2026-04-12T12:00:00+00:00",
            "market": "CLI",
            "position": "YES",
            "entry_price": 0.5,
            "capital_allocated": 100.0,
        }
        normalize_position_sizing_meta(
            sample,
            source_path="cli_validate_sample",
            mutate_capital=False,
            record_audit=False,
        )
        inv = validate_trade_open_invariants(sample, live=False)
        ok = bool(inv.get("ok"))
        errors = inv.get("errors") or []
        out = {
            "ok": ok,
            "summary": "invariants_pass" if ok else "invariants_failed",
            "errors": errors,
            "risk_bucket_at_open": sample.get("risk_bucket_at_open"),
            "normalized_meta": sample.get("position_sizing_meta"),
        }
        print(json.dumps(out, indent=2, default=str))
        return 0 if ok else 1

    if args.cmd == "simulate":
        out = simulate_sizing_cli(args.size, args.bucket, trade_id=args.trade_id)
        if args.trade_id:
            out = {**out, "trade_id": args.trade_id}
        print(json.dumps(out, indent=2, default=str))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main_sizing())
