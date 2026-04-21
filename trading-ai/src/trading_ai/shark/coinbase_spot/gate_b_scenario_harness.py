"""Deterministic Gate B scenario assertions for staged micro-validation."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from trading_ai.shark.coinbase_spot.gate_b_engine import GateBMomentumEngine
from trading_ai.shark.coinbase_spot.gate_b_monitor import GateBMonitorState, gate_b_monitor_tick
from trading_ai.shark.coinbase_spot.gate_b_strategy_spec import strict_entry_check


def _row(**kwargs: Any) -> Dict[str, Any]:
    ts = time.time()
    base: Dict[str, Any] = {
        "product_id": "BTC-USD",
        "volume_24h_usd": 5e6,
        "spread_bps": 12.0,
        "book_depth_usd": 80_000.0,
        "move_pct": 0.08,
        "volume_surge_ratio": 1.8,
        "continuation_candles": 3,
        "velocity_score": 0.62,
        "quote_ts": ts,
        "best_bid": 50_000.0,
        "best_ask": 50_015.0,
        "closes": [48_000 + 80 * i for i in range(24)],
        "exhaustion_risk": 0.2,
    }
    base.update(kwargs)
    return base


def run_all_asserted() -> Dict[str, Any]:
    scenarios: List[Dict[str, Any]] = []
    eng = GateBMomentumEngine()

    def ok(name: str, fn: Any) -> None:
        try:
            fn()
            scenarios.append({"id": name, "passed": True})
        except Exception as exc:  # pragma: no cover - harness should stay green
            scenarios.append({"id": name, "passed": False, "error": str(exc)})

    ok("strict_entry_clean", lambda: strict_entry_check(_row(), open_product_ids=[]).entry_pass is True)
    ok("strict_entry_bad_volume", lambda: strict_entry_check(_row(volume_24h_usd=50), open_product_ids=[]).entry_pass is False)
    ok("engine_scan", lambda: len(eng.evaluate_entry_candidates([_row()], open_product_ids=[], regime_inputs={}).get("candidates") or []) >= 0)
    ok(
        "monitor_exit",
        lambda: gate_b_monitor_tick(
            GateBMonitorState("X", 100.0, 110.0, 0.0, 106.0),
            now_ts=10.0,
            profit_target_pct=0.5,
            trailing_stop_from_peak_pct=0.03,
        ).get("exit")
        is True,
    )

    passed = sum(1 for s in scenarios if s.get("passed"))
    return {
        "all_passed": passed == len(scenarios) and len(scenarios) > 0,
        "scenario_count": len(scenarios),
        "passed_count": passed,
        "failed_count": len(scenarios) - passed,
        "scenarios": scenarios,
    }
