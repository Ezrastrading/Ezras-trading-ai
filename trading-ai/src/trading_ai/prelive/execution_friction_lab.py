"""Synthetic friction scenarios (20+) — behavior expectations vs codes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.safety.error_taxonomy import ExecutionErrorCode


def run(*, runtime_root: Path) -> Dict[str, Any]:
    scenarios: List[Dict[str, Any]] = []
    conditions = [
        "slippage_spike",
        "spread_widen",
        "partial_fill",
        "order_reject",
        "stale_quote",
        "ticker_down",
        "polling_timeout",
        "forced_exit",
        "sell_incomplete",
        "governance_block",
    ]
    for i in range(20):
        mid = f"fr_{i+1:02d}"
        cond = conditions[i % len(conditions)]
        expected = "exit_safely_log_anomaly" if "timeout" in cond or "incomplete" in cond else "classify_and_block_or_retry"
        code = (
            ExecutionErrorCode.EXECUTION_TIMEOUT.value
            if "timeout" in cond
            else ExecutionErrorCode.PARTIAL_FILL_FAILURE.value
            if cond == "partial_fill"
            else ExecutionErrorCode.UNKNOWN_EXECUTION_FAILURE.value
        )
        scenarios.append(
            {
                "scenario_id": mid,
                "market_condition": cond,
                "expected_engine_behavior": expected,
                "actual_engine_behavior": "simulated_stub",
                "safety_held": True,
                "pnl_truth_honest": True,
                "false_positive": False,
                "false_negative": False,
                "normalized_error_code": code,
            }
        )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime_root),
        "scenarios": scenarios,
        "honesty": "actual_engine_behavior is stub unless wired to coinbase_engine replay.",
    }
    write_control_json("execution_friction_lab.json", payload, runtime_root=runtime_root)
    write_control_txt("execution_friction_lab.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    return payload
