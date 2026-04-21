"""Mock venue scenarios — drives failsafe + ledger + error taxonomy (dry)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.runtime.trade_ledger import append_trade_ledger_line
from trading_ai.safety.error_taxonomy import normalize_error_code
from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks, write_kill_switch


def _scenario(
    sid: str,
    desc: str,
    *,
    runtime_root: Path,
    kill: bool = False,
    multi_leg: bool = False,
    product_id: str = "BTC-USD",
    quote: float = 25.0,
    balances: Dict[str, float] | None = None,
    skip_governance: bool = True,
) -> Dict[str, Any]:
    write_kill_switch(False, note="harness_reset", runtime_root=runtime_root)
    if kill:
        write_kill_switch(True, note="harness", runtime_root=runtime_root)
    ctx = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id=product_id,
        gate="gate_a",
        quote_notional=quote,
        base_size=None,
        quote_balances_by_ccy=balances or {"USD": 1_000_000.0},
        strategy_id="harness",
        trade_id=f"mock_{uuid.uuid4().hex[:8]}",
        multi_leg=multi_leg,
        skip_governance=skip_governance,
    )
    ok, code, msg = run_failsafe_checks(ctx, runtime_root=runtime_root)
    ledger_id = None
    if ok and not kill:
        line = append_trade_ledger_line(
            {
                "trade_id": ctx.trade_id,
                "avenue_id": "coinbase",
                "gate_id": "gate_a",
                "product_id": product_id,
                "quote_used": quote,
                "notional": quote,
                "execution_status": "dry_run",
                "validation_status": "mock",
                "failure_reason": None,
            },
            runtime_root=runtime_root,
        )
        ledger_id = line.get("trade_id")
    return {
        "scenario_id": sid,
        "description": desc,
        "failsafe_ok": ok,
        "error_code": normalize_error_code(code if not ok else "ok"),
        "message": msg,
        "ledger_appended": ledger_id is not None,
        "silent_skip": False,
    }


def run(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.control.system_execution_lock import ensure_system_execution_lock_file

    ensure_system_execution_lock_file(runtime_root=runtime_root)
    scenarios: List[Dict[str, Any]] = []
    scenarios.append(_scenario("s01", "accepted buy path", runtime_root=runtime_root, kill=False))
    scenarios.append(_scenario("s02", "kill switch", runtime_root=runtime_root, kill=True))
    scenarios.append(_scenario("s03", "multi-leg blocked", runtime_root=runtime_root, multi_leg=True))
    scenarios.append(
        _scenario(
            "s04",
            "insufficient USDC for ETH-USDC",
            runtime_root=runtime_root,
            product_id="ETH-USDC",
            balances={"USD": 1000.0, "USDC": 1.0},
        )
    )
    scenarios.append(
        _scenario(
            "s05",
            "alternate product to avoid duplicate window vs s01",
            runtime_root=runtime_root,
            kill=False,
            product_id="SOL-USD",
        )
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime_root),
        "scenarios": scenarios,
        "contradictions": [],
        "honesty": "Harness uses skip_governance=True for deterministic local runs; live must set enforcement explicitly.",
    }
    write_control_json("mock_execution_harness_results.json", payload, runtime_root=runtime_root)
    write_control_txt(
        "mock_execution_harness_results.txt",
        json.dumps(payload, indent=2) + "\n",
        runtime_root=runtime_root,
    )
    return payload
