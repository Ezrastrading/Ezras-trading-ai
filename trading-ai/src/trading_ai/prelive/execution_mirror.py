"""Execution mirror (dry) — shared by script and prelive orchestrator."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.control.system_execution_lock import ensure_system_execution_lock_file
from trading_ai.runtime.live_execution_state import record_execution_step
from trading_ai.runtime.trade_ledger import append_trade_ledger_line
from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks, write_kill_switch


def run(*, runtime_root: Path) -> Dict[str, Any]:
    ensure_system_execution_lock_file(runtime_root=runtime_root)
    write_kill_switch(False, runtime_root=runtime_root)

    trades: List[Dict[str, Any]] = []
    for i in range(18):
        tid = f"mirror_{uuid.uuid4().hex[:10]}"
        pid = "BTC-USD"
        ctx = FailsafeContext(
            action="place_market_entry",
            avenue_id="coinbase",
            product_id=pid,
            gate="gate_a",
            quote_notional=15.0 + i * 0.5,
            base_size=None,
            quote_balances_by_ccy={"USD": 1e6, "USDC": 1e6},
            strategy_id="mirror",
            trade_id=tid,
            multi_leg=False,
            skip_governance=True,
            skip_duplicate_guard=True,
        )
        ok, code, msg = run_failsafe_checks(ctx, runtime_root=runtime_root)
        record_execution_step(
            step=f"mirror_preflight_{i}",
            avenue="coinbase",
            gate="gate_a",
            mode="validating",
            trade_id=tid,
            success=ok,
            error="" if ok else msg,
            runtime_root=runtime_root,
        )
        if ok:
            append_trade_ledger_line(
                {
                    "trade_id": tid,
                    "avenue_id": "coinbase",
                    "gate_id": "gate_a",
                    "product_id": pid,
                    "quote_used": ctx.quote_notional,
                    "notional": ctx.quote_notional,
                    "execution_status": "dry_run_mirror",
                    "validation_status": "mirror_ok",
                    "failure_reason": None,
                },
                runtime_root=runtime_root,
            )
        trades.append({"trade_id": tid, "ok": ok, "code": code, "msg": msg})

    out: Dict[str, Any] = {
        "ok": all(t["ok"] for t in trades),
        "trades": trades,
        "count": len(trades),
        "honesty": "No HTTP calls; governance skipped for deterministic mirror.",
    }
    p = runtime_root / "data" / "control" / "execution_mirror_results.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out
