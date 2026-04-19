#!/usr/bin/env python3
"""
End-to-end dry run: no live credentials.
Initializes NTE memory, simulates a closed trade path, refreshes health.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main() -> None:
    from trading_ai.shark.dotenv_load import load_shark_dotenv

    load_shark_dotenv()

    # Isolate runtime for dry-run
    root = tempfile.mkdtemp(prefix="nte_dry_run_")
    os.environ["EZRAS_RUNTIME_ROOT"] = root
    os.environ["NTE_EXECUTION_MODE"] = "paper"
    os.environ.pop("NTE_LIVE_TRADING_ENABLED", None)

    from trading_ai.nte.capital_ledger import append_realized, load_ledger
    from trading_ai.nte.config.config_validator import validate_mode_safety, validate_nte_settings
    from trading_ai.nte.hardening.memory_integrity_checker import run_integrity_scan
    from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted
    from trading_ai.nte.memory.store import MemoryStore
    from trading_ai.nte.reports.system_health_reporter import refresh_default_health
    from trading_ai.nte.research.research_firewall import promotion_allowed

    print("1. Initialize memory …")
    store = MemoryStore()
    store.ensure_defaults()

    ok, errs = validate_nte_settings()
    assert ok, errs
    validate_mode_safety(strict=False)

    print("2. Integrity scan …")
    scan = run_integrity_scan(store)
    assert all(x["ok"] for x in scan), scan

    print("3. Simulate Coinbase trade + close …")
    store.append_trade(
        {
            "avenue": "coinbase",
            "product": "BTC-USD",
            "side": "buy",
            "usd": 10.0,
            "dry_run": True,
        }
    )
    store.append_trade(
        {
            "avenue": "coinbase",
            "product": "BTC-USD",
            "side": "sell",
            "usd": 10.05,
            "pnl_usd": 0.05,
            "dry_run": True,
        }
    )

    print("4. Capital ledger …")
    append_realized(0.05, avenue="coinbase", label="dry_run_close", fees_usd=0.01)
    led = load_ledger()
    print(f"   realized (net): {led.get('realized_pnl_usd')}")

    print("5. Research firewall …")
    assert promotion_allowed("sandbox_strat_x", passed_checks=False) is False
    assert promotion_allowed("candidate_ok", passed_checks=True) is True

    print("6. Live order guard (expect failure in paper) …")
    try:
        assert_live_order_permitted(
            "place_market_entry",
            "coinbase",
            "BTC-USD",
            source="dry_run_smoke",
            quote_notional=10.0,
            order_side="BUY",
        )
    except RuntimeError:
        print("   Live correctly blocked in paper mode.")

    print("7. System health …")
    h = refresh_default_health()
    print(f"   healthy={h.get('healthy')} mode={h.get('mode')}")

    print("8. Done — dry run root:", root)
    print("OK: dry_run_smoke_test completed without live credentials.")


if __name__ == "__main__":
    main()
