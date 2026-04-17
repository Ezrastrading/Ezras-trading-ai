#!/usr/bin/env python3
"""
Complete sell smoke test.

Tests formatting, min-size rules, holdings verification, and (optionally) live sells.
Run from repo root::

    PYTHONPATH=src python3 scripts/test_sell_complete.py

On Railway SSH (destructive if live sells enabled)::

    cd /app && PYTHONPATH=src python3 scripts/test_sell_complete.py

Live market sells (sections 5–6) run only when ``COINBASE_SELL_SMOKE_LIVE=1``
and the client has API credentials. Otherwise those steps are skipped.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

logging.basicConfig(level=logging.WARNING)

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

from trading_ai.shark.coinbase_accumulator import (  # noqa: E402
    CoinbaseAccumulator,
    _PRODUCT_BASE_PRECISION,
    _enforce_min_base_for_sell,
    _fmt_base_size,
    _min_base_size_for_product,
)
from trading_ai.shark.outlets.coinbase import CoinbaseClient  # noqa: E402

LIVE_SELLS = os.environ.get("COINBASE_SELL_SMOKE_LIVE", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _pass_emoji(ok: bool) -> str:
    return "OK" if ok else "FAIL"


def main() -> int:
    print("=" * 50)
    print("SELL SMOKE TEST")
    print("=" * 50)

    acc = CoinbaseAccumulator()
    client = acc._client
    has_creds = bool(client.has_credentials())

    if not has_creds:
        print("\nWARN: No Coinbase API credentials — holdings/sell tests will be limited.\n")

    # TEST 1: Format precision per coin
    print("\n1. FORMAT PRECISION TEST:")
    test_cases = [
        ("BTC-USD", 0.00012345678),
        ("ETH-USD", 0.00081234),
        ("DOGE-USD", 19.567),
        ("ADA-USD", 15.12345678),
        ("SOL-USD", 0.022155),
        ("XRP-USD", 1.315368),
        ("LINK-USD", 0.6),
        ("DOT-USD", 3.61227),
        ("AVAX-USD", 0.188023),
        ("UNI-USD", 0.598072),
    ]
    all_ok = True
    for pid, size in test_cases:
        formatted = _fmt_base_size(pid, size)
        min_s = _min_base_size_for_product(pid)
        try:
            fv = float(formatted)
        except ValueError:
            fv = 0.0
        ok = fv + 1e-18 >= min_s
        status = _pass_emoji(ok)
        print(
            f"  [{status}] {pid}: {size} -> {formatted} "
            f"(min={min_s}, prec={_PRODUCT_BASE_PRECISION.get(pid, 8)})"
        )
        if not ok:
            all_ok = False
    print(f"  Result: {'PASS' if all_ok else 'FAIL'}")

    # TEST 2: Min size enforcement (expectations follow live ``_min_base_size_for_product``)
    print("\n2. MIN SIZE ENFORCEMENT TEST:")
    min_tests = [
        ("DOGE-USD", 0.05),
        ("DOGE-USD", 0.1),
        ("DOGE-USD", 19.5),
        ("ADA-USD", 0.5),
        ("ADA-USD", 1.0),
        ("BTC-USD", 0.000001),
    ]
    all_ok = True
    for pid, size in min_tests:
        min_sz = _min_base_size_for_product(pid)
        enforced = _enforce_min_base_for_sell(pid, size)
        expect_ok = size + 1e-15 >= min_sz
        ok = (enforced > 0) == expect_ok
        status = _pass_emoji(ok)
        print(
            f"  [{status}] {pid} size={size} enforced={enforced:.12f} "
            f"min={min_sz} expect_ok={expect_ok}"
        )
        if not ok:
            all_ok = False
    print(f"  Result: {'PASS' if all_ok else 'FAIL'}")

    holdings: dict[str, float] = {}

    # TEST 3: Holdings from API
    print("\n3. HOLDINGS CHECK TEST:")
    if has_creds:
        try:
            j = client._request("GET", "/accounts")
            for a in j.get("accounts", []) or []:
                curr = str(a.get("currency", "") or "")
                ab = a.get("available_balance")
                if not isinstance(ab, dict):
                    ab = {}
                val = float(ab.get("value", 0) or 0)
                if val > 0 and curr not in ("USD", "USDC", "USDT"):
                    holdings[curr] = val
                    print(f"  Holdings: {curr} = {val:.8f}")
        except Exception as exc:
            print(f"  ERROR: /accounts failed: {exc}")
    else:
        print("  (skipped — no credentials)")

    if not holdings:
        print("  No crypto holdings found (or API skipped).")
    else:
        print(f"  Total coins: {len(holdings)}")

    # TEST 4: Verify holdings method
    print("\n4. VERIFY HOLDINGS METHOD:")
    if has_creds and holdings:
        for curr, expected in list(holdings.items())[:3]:
            pid = f"{curr}-USD"
            available = acc._verify_holdings(pid, expected)
            match = abs(available - expected) < max(1e-6, expected * 1e-6)
            print(
                f"  [{'OK' if match else 'FAIL'}] {pid}: "
                f"expected={expected:.8f} got={available:.8f}"
            )
    else:
        print("  (skipped)")

    # TEST 5: Actual sell
    print("\n5. ACTUAL SELL TEST:")
    if not LIVE_SELLS:
        print("  SKIP: set COINBASE_SELL_SMOKE_LIVE=1 to run live sells (destructive).")
    elif not has_creds:
        print("  SKIP: no credentials.")
    elif holdings:
        viable: list[tuple[str, float]] = []
        for c, sz in holdings.items():
            p = f"{c}-USD"
            if _enforce_min_base_for_sell(p, sz) > 0:
                viable.append((c, sz))
        if not viable:
            print(
                "  SKIP: every holding is below exchange minimum (dust) — "
                "no live sell attempted"
            )
        else:
            # Smallest *sellable* lot (min avoids picking dust that only fails below_min).
            test_curr, test_size = min(viable, key=lambda x: x[1])
            test_pid = f"{test_curr}-USD"

            print(f"  Testing sell: {test_pid} size={test_size:.8f}")

            enforced = _enforce_min_base_for_sell(test_pid, test_size)

            if enforced <= 0:
                print("  WARN: Size below minimum — skipping actual sell")
            else:
                print(f"  Attempting sell of {enforced:.8f} {test_curr}...")
                result = acc._try_market_sell_twice(
                    test_pid,
                    _fmt_base_size(test_pid, enforced),
                    size_base_from_pos=test_size,
                )
                if result.success:
                    print(
                        f"  [OK] SELL SUCCESS: {test_pid} "
                        f"order_id={result.order_id}"
                    )
                elif result.reason in (
                    "below_min_size",
                    "insufficient_holdings",
                ):
                    print(f"  [SKIP] {test_pid}: {result.reason}")
                else:
                    print(
                        f"  [FAIL] SELL FAILED: {test_pid} "
                        f"reason={result.reason}"
                    )
    else:
        print("  No holdings to test sell")

    # TEST 6: Multi-sell stress
    print("\n6. MULTI-SELL STRESS TEST:")
    if not LIVE_SELLS:
        print("  SKIP: set COINBASE_SELL_SMOKE_LIVE=1 for live multi-sell.")
    elif not has_creds:
        print("  SKIP: no credentials.")
    elif len(holdings) >= 2:
        results: list[tuple[str, bool, str | None]] = []
        coins = list(holdings.items())[:4]
        for curr, size in coins:
            pid = f"{curr}-USD"
            enforced = _enforce_min_base_for_sell(pid, size)
            if enforced > 0:
                r = acc._try_market_sell_twice(
                    pid,
                    _fmt_base_size(pid, enforced),
                    size_base_from_pos=size,
                )
                results.append((pid, r.success, r.reason))
                time.sleep(0.2)

        wins = sum(1 for _, s, _ in results if s)
        print(f"  Sells attempted: {len(results)}")
        print(f"  Successful: {wins}/{len(results)}")
        for pid, success, reason in results:
            if success:
                tag = "OK"
            elif reason in ("below_min_size", "insufficient_holdings"):
                tag = "SKIP"
            else:
                tag = "FAIL"
            print(f"  [{tag}] {pid}: {reason}")
    else:
        print("  Need 2+ holdings for stress test (or skipped).")

    # TEST 7: Final balance
    print("\n7. FINAL BALANCE CHECK:")
    if has_creds:
        try:
            bal = client.get_usd_balance()
            print(f"  USD Balance: ${bal:.2f}")
        except Exception as exc:
            print(f"  USD balance error: {exc}")
        try:
            new_j = client._request("GET", "/accounts")
            remaining: list[str] = []
            for a in new_j.get("accounts", []) or []:
                curr = str(a.get("currency", "") or "")
                ab = a.get("available_balance")
                if not isinstance(ab, dict):
                    ab = {}
                val = float(ab.get("value", 0) or 0)
                if val > 0 and curr not in ("USD", "USDC", "USDT"):
                    remaining.append(f"{curr}={val:.4f}")
            if remaining:
                print(f"  Remaining crypto: {', '.join(remaining)}")
            else:
                print("  No crypto remaining (or only fiat).")
        except Exception as exc:
            print(f"  Final accounts error: {exc}")
    else:
        print("  (skipped — no credentials)")

    print("\n" + "=" * 50)
    print("SMOKE TEST COMPLETE")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
