#!/usr/bin/env python3
"""
One-shot: cancel every Kalshi resting order (open limit orders not yet filled).

Loads env via ``load_shark_dotenv`` (project ``.env``). Requires Kalshi credentials.

Run from ``trading-ai`` repo root:
  PYTHONPATH=src python3 scripts/cancel_all_kalshi_resting_orders.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "src") not in os.environ.get("PYTHONPATH", ""):
    sys.path.insert(0, str(_REPO / "src"))


def main() -> int:
    from trading_ai.shark.dotenv_load import load_shark_dotenv

    load_shark_dotenv()

    from trading_ai.shark.outlets.kalshi import KalshiClient

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        print("error: Kalshi credentials missing (KALSHI_API_KEY / KALSHI_ACCESS_KEY_ID)", file=sys.stderr)
        return 2

    try:
        orders = client.list_resting_orders()
    except Exception as exc:
        print(f"error: list_resting_orders failed: {exc}", file=sys.stderr)
        return 3

    cancelled = 0
    errors = 0
    for o in orders:
        oid = str(o.get("order_id") or "").strip()
        ticker = str(o.get("ticker") or "").strip() or "?"
        if not oid:
            continue
        try:
            client.cancel_order(oid)
            cancelled += 1
            print(f"cancelled {oid} [{ticker}]")
        except Exception as exc:
            errors += 1
            print(f"error cancelling {oid} [{ticker}]: {exc}", file=sys.stderr)

    print(f"done: {cancelled} cancelled, {errors} errors, {len(orders)} listed")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
