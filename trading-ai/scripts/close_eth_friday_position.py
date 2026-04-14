#!/usr/bin/env python3
"""
URGENT: Close the Friday ETH (KXETH / multi-day) position immediately.

Steps:
  1. List Kalshi portfolio positions.
  2. Find every KXETH* position (or any ticker matching KALSHI_CLOSE_TICKER_PREFIX).
  3. Cancel any resting orders for those tickers.
  4. Place a market-sell for the held shares to exit.

Run from repo root:
  PYTHONPATH=src python3 scripts/close_eth_friday_position.py

Override which tickers to target:
  KALSHI_CLOSE_TICKER_PREFIX="KXETH" PYTHONPATH=src python3 scripts/close_eth_friday_position.py
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
        print("ERROR: Kalshi credentials missing (KALSHI_API_KEY / KALSHI_ACCESS_KEY_ID)", file=sys.stderr)
        return 2

    # Which ticker prefixes to close — default: all KXETH crypto markets.
    prefix_env = (os.environ.get("KALSHI_CLOSE_TICKER_PREFIX") or "KXETH").strip().upper()
    prefixes = [p.strip() for p in prefix_env.split(",") if p.strip()]
    print(f"Targeting tickers with prefixes: {prefixes}")

    # ── 1. Fetch live portfolio positions from Kalshi API ─────────────────────
    try:
        pos_data = client.list_portfolio_positions()
    except Exception as exc:
        print(f"ERROR: list_portfolio_positions failed: {exc}", file=sys.stderr)
        return 3

    target_positions = []
    for p in pos_data:
        ticker = str(p.get("ticker") or p.get("market_id") or "").strip().upper()
        if any(ticker.startswith(pfx) for pfx in prefixes):
            # Only include if there are actual contracts held (position_fp != 0)
            pos_fp = float(p.get("position_fp") or 0)
            if pos_fp != 0.0:
                target_positions.append(p)

    if not target_positions:
        print(f"No open positions found matching {prefixes}. Nothing to close.")
        # Still cancel any resting orders just in case.
    else:
        print(f"Found {len(target_positions)} position(s) to close:")
        for p in target_positions:
            pos_fp = float(p.get("position_fp") or 0)
            side = "yes" if pos_fp > 0 else "no"
            print(f"  ticker={p.get('ticker')}  side={side}  "
                  f"position_fp={pos_fp}  exposure=${p.get('market_exposure_dollars')}")

    # ── 2. Cancel resting orders for matching tickers ─────────────────────────
    try:
        resting = client.list_resting_orders()
    except Exception as exc:
        print(f"WARNING: list_resting_orders failed: {exc}", file=sys.stderr)
        resting = []

    cancelled = 0
    for o in resting:
        ticker = str(o.get("ticker") or "").strip().upper()
        oid = str(o.get("order_id") or "").strip()
        if not oid:
            continue
        if any(ticker.startswith(pfx) for pfx in prefixes):
            try:
                client.cancel_order(oid)
                cancelled += 1
                print(f"Cancelled resting order {oid} [{ticker}]")
            except Exception as exc:
                print(f"WARNING: cancel {oid} [{ticker}] failed: {exc}", file=sys.stderr)

    if cancelled:
        print(f"Cancelled {cancelled} resting order(s).")

    # ── 3. Sell filled positions ───────────────────────────────────────────────
    sold = 0
    errors = 0
    for p in target_positions:
        ticker = str(p.get("ticker") or "").strip()
        # position_fp: positive = YES contracts, negative = NO contracts
        pos_fp = float(p.get("position_fp") or 0)
        side = "yes" if pos_fp > 0 else "no"
        contracts = int(abs(pos_fp) + 0.5)

        if contracts <= 0:
            print(f"  {ticker}: no contracts to sell (position_fp={pos_fp}), skipping sell.")
            continue

        print(f"  Selling {contracts} {side.upper()} contracts of {ticker} at market …")
        try:
            res = client.place_order(
                ticker=ticker,
                side=side,
                count=contracts,
                action="sell",
            )
            if res.filled_size and res.filled_size > 0:
                print(f"  ✓ Sold {res.filled_size} @ {res.filled_price:.3f}  order_id={res.order_id}")
                sold += 1
            else:
                print(f"  ⚠ Order placed but fill_size=0 (order_id={res.order_id}, "
                      f"status={res.status}). Check Kalshi dashboard.")
        except Exception as exc:
            errors += 1
            print(f"  ERROR: sell failed for {ticker}: {exc}", file=sys.stderr)

    print()
    print(f"Summary: {len(target_positions)} position(s) found, {sold} sold, "
          f"{cancelled} resting orders cancelled, {errors} sell errors.")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
