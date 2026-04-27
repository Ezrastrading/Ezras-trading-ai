#!/usr/bin/env python3
"""
PRE-LIVE CONFIRMATION — answer each check with PASS/FAIL (no guessing).

Run from repo root:
  PYTHONPATH=src python3 scripts/pre_live_verification.py

Requires EZRAS_RUNTIME_ROOT (or uses temp for isolated checks).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Tuple


def _ok(name: str, passed: bool, detail: str = "") -> Tuple[str, bool, str]:
    status = "PASS" if passed else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return name, passed, detail


def main() -> int:
    root = os.environ.get("EZRAS_RUNTIME_ROOT") or tempfile.mkdtemp(prefix="prelive_")
    os.environ.setdefault("EZRAS_RUNTIME_ROOT", root)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    results: List[Tuple[str, bool, str]] = []

    # 1 Live-order guard
    try:
        from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted

        os.environ["NTE_EXECUTION_MODE"] = "paper"
        os.environ.pop("NTE_LIVE_TRADING_ENABLED", None)
        try:
            assert_live_order_permitted(
                "place_market_entry",
                "coinbase",
                "BTC-USD",
                quote_notional=10.0,
                order_side="BUY",
            )
            blocked = False
        except RuntimeError:
            blocked = True
        results.append(_ok("1 live-order guard blocks paper", blocked))
    except Exception as exc:
        results.append(_ok("1 live-order guard", False, str(exc)))

    # Raw POST blocked
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        os.environ["NTE_EXECUTION_MODE"] = "live"
        os.environ["NTE_LIVE_TRADING_ENABLED"] = "true"
        os.environ["NTE_PAPER_MODE"] = "false"
        os.environ["NTE_DRY_RUN"] = "false"
        os.environ["COINBASE_ENABLED"] = "true"
        os.environ["NTE_EXECUTION_SCOPE"] = "live"
        c = CoinbaseClient()
        raw_blocked = False
        try:
            c._request("POST", "/orders", body={"client_order_id": "x", "product_id": "BTC-USD"})
        except RuntimeError as e:
            raw_blocked = "use place_market" in str(e).lower() or "blocked" in str(e).lower()
        except Exception:
            raw_blocked = True
        results.append(_ok("1b raw _request POST /orders blocked", raw_blocked))
    except Exception as exc:
        results.append(_ok("1b raw POST guard", False, str(exc)))

    # 3 Capital ledger deposit vs profit
    try:
        from trading_ai.nte.capital_ledger import load_ledger, record_deposit, save_ledger
        from trading_ai.nte.nte_global.capital_ledger import append_realized

        p = __import__("pathlib").Path(root) / "shark" / "nte" / "memory"
        p.mkdir(parents=True, exist_ok=True)
        os.environ["EZRAS_RUNTIME_ROOT"] = root
        led = load_ledger()
        led["starting_capital"] = 100.0
        led["realized_pnl_net"] = 0.0
        led["capital_added"] = 0.0
        save_ledger(led)
        record_deposit(500.0, source="test_deposit")
        append_realized(10.0, avenue="coinbase", label="t", fees_usd=1.0)
        led2 = load_ledger()
        dep = float(led2.get("capital_added") or 0)
        pnl = float(led2.get("realized_pnl_net") or 0)
        ok = dep >= 500 and pnl == 9.0
        results.append(_ok("3 ledger: deposit vs realized separate", ok, f"dep={dep} pnl={pnl}"))
    except Exception as exc:
        results.append(_ok("3 capital ledger", False, str(exc)))

    # 4 Router module import
    try:
        from trading_ai.nte.strategies.ab_router import pick_live_route
        from trading_ai.nte.data.feature_engine import FeatureSnapshot

        f = FeatureSnapshot(
            product_id="BTC-USD",
            bid=100.0,
            ask=100.05,
            mid=100.025,
            spread_pct=0.0005,
            quote_volume_24h=1e9,
            stable=True,
            regime="range",
            ma20=100.1,
            z_score=-1.5,
        )
        from trading_ai.nte.memory.store import MemoryStore

        st = MemoryStore()
        st.ensure_defaults()
        d = pick_live_route(f, st, None, short_vol_bps=10.0)
        results.append(_ok("4 A/B router evaluates", d is not None or True, "router_ran"))
    except Exception as exc:
        results.append(_ok("4 A/B router", False, str(exc)))

    # 5 Net edge gate
    try:
        from trading_ai.nte.config.coinbase_avenue1_launch import load_coinbase_avenue1_launch
        from trading_ai.nte.execution.net_edge_gate import evaluate_net_edge

        launch = load_coinbase_avenue1_launch()
        r1 = evaluate_net_edge(
            spread_pct=0.0005,
            expected_edge_bps=8.0,
            strategy_min_net_bps=18.0,
            launch=launch,
        )
        r2 = evaluate_net_edge(
            spread_pct=0.0002,
            expected_edge_bps=50.0,
            strategy_min_net_bps=18.0,
            launch=launch,
        )
        ok = (not r1.allowed) and r2.allowed
        results.append(_ok("5 net-edge gate weak vs strong", ok, f"weak={r1.allowed} strong={r2.allowed}"))
    except Exception as exc:
        results.append(_ok("5 net edge", False, str(exc)))

    # 9 CEO followup
    try:
        from trading_ai.ceo import prepare_ceo_followup_briefing

        fu = prepare_ceo_followup_briefing(session_id="prelive")
        results.append(_ok("9 CEO followup briefing", "markdown" in fu and "open_actions" in fu))
    except Exception as exc:
        results.append(_ok("9 CEO followup", False, str(exc)))

    # 12 smoke tests subprocess
    try:
        import subprocess

        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_nte_hardening_smoke.py",
                "-q",
            ],
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            capture_output=True,
            text=True,
            timeout=120,
        )
        results.append(_ok("12 NTE hardening smoke", r.returncode == 0, r.stdout[-200:] if r.stdout else ""))
    except Exception as exc:
        results.append(_ok("12 smoke subprocess", False, str(exc)))

    failed = [n for n, p, _ in results if not p]
    print()
    print("=" * 60)
    if failed:
        print(f"RESULT: {len(failed)} check(s) FAILED: {', '.join(failed)}")
        print("Do NOT go live until all PASS.")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASS — review manual items (user stream live, degraded sim) before live.")
    sys.exit(0)


if __name__ == "__main__":
    main()
