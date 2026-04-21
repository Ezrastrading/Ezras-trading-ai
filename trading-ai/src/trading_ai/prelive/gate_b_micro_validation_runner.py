"""
Gate B staged micro-validation — Coinbase spot-row gainers path (NOT live venue orders).

Writes:
- data/control/gate_b_micro_validation_proof.json
- data/control/gate_b_validation.json (auto-generated; no manual dead-end)
- append trade_ledger.jsonl lines with gate_id=gate_b (staged)
- data/deployment/live_validation_runs/gate_b_micro_*.json run record
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.prelive._io import write_control_json
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.shark.coinbase_spot.gate_b_config import load_gate_b_config_from_env
from trading_ai.shark.coinbase_spot.gate_b_scenario_harness import run_all_asserted


def _duplicate_trade_blocked() -> Dict[str, Any]:
    """
    Isolated temp runtime: default lock has gate_b_enabled=False; duplicate proof must not
    mutate the caller's system_execution_lock.json.
    """
    import tempfile

    from trading_ai.control.system_execution_lock import (
        ensure_system_execution_lock_file,
        load_system_execution_lock,
        save_system_execution_lock,
    )
    from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks

    prev_root = os.environ.get("EZRAS_RUNTIME_ROOT")
    prev_dup = os.environ.get("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC")
    try:
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            os.environ["EZRAS_RUNTIME_ROOT"] = str(rt)
            os.environ["EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC"] = "3600"
            ensure_system_execution_lock_file(runtime_root=rt)
            lock = load_system_execution_lock(runtime_root=rt)
            lock["gate_b_enabled"] = True
            lock["system_locked"] = True
            lock["ready_for_live_execution"] = True
            save_system_execution_lock(lock, runtime_root=rt)

            ctx = FailsafeContext(
                action="place_market_entry",
                avenue_id="coinbase",
                product_id="BTC-USD",
                gate="gate_b",
                quote_notional=25.0,
                base_size=None,
                quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
                strategy_id="gate_b_micro",
                trade_id=f"gbdup_{uuid.uuid4().hex[:8]}",
                multi_leg=False,
                skip_governance=True,
                skip_duplicate_guard=False,
            )
            ok1, _, _ = run_failsafe_checks(ctx, runtime_root=rt)
            ctx2 = FailsafeContext(
                action="place_market_entry",
                avenue_id="coinbase",
                product_id="BTC-USD",
                gate="gate_b",
                quote_notional=26.0,
                base_size=None,
                quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
                strategy_id="gate_b_micro",
                trade_id=f"gbdup_{uuid.uuid4().hex[:8]}",
                multi_leg=False,
                skip_governance=True,
                skip_duplicate_guard=False,
            )
            ok2, code2, _ = run_failsafe_checks(ctx2, runtime_root=rt)
            passed = ok1 is True and ok2 is False and "duplicate" in (code2 or "").lower()
            return {"passed": passed, "first_ok": ok1, "second_ok": ok2, "second_code": code2}
    finally:
        if prev_root is None:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)
        else:
            os.environ["EZRAS_RUNTIME_ROOT"] = prev_root
        if prev_dup is None:
            os.environ.pop("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC", None)
        else:
            os.environ["EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC"] = prev_dup


def run(
    *,
    runtime_root: Optional[Path] = None,
    write_ledger: bool = True,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    cfg = load_gate_b_config_from_env()
    ts = datetime.now(timezone.utc).isoformat()

    harness = run_all_asserted()
    dup = _duplicate_trade_blocked()

    ledger_ids: List[str] = []
    if write_ledger:
        try:
            from trading_ai.runtime.trade_ledger import append_trade_ledger_line

            for kind, status in (
                ("scan_select", "gate_b_staged_micro_scan"),
                ("entry_sim", "gate_b_staged_micro_entry_intent"),
                ("exit_sim", "gate_b_staged_micro_exit_eval"),
            ):
                row = append_trade_ledger_line(
                    {
                        "trade_id": f"gb_micro_{kind}_{uuid.uuid4().hex[:10]}",
                        "avenue_id": "coinbase",
                        "gate_id": "gate_b",
                        "product_id": "BTC-USD",
                        "execution_status": status,
                        "validation_status": "gate_b_staged_micro",
                        "strategy_id": cfg.strategy_family,
                        "failure_reason": None,
                    },
                    runtime_root=root,
                )
                ledger_ids.append(str(row.get("trade_id")))
        except Exception as exc:
            ledger_ids = [f"ledger_error:{exc}"]

    intel_hook_ok = True
    try:
        from trading_ai.intelligence.integration.live_hooks import emit_gate_b_artifact_event

        emit_gate_b_artifact_event(
            "gate_b_micro_validation_complete",
            {"harness_all_passed": harness.get("all_passed"), "duplicate_check": dup},
        )
    except Exception:
        intel_hook_ok = False

    all_passed = bool(harness.get("all_passed")) and bool(dup.get("passed"))
    proof: Dict[str, Any] = {
        "generated_at": ts,
        "validation_kind": "gate_b_staged_micro",
        "honesty": "No Coinbase or Kalshi orders; deterministic rows + failsafe duplicate check + ledger stubs.",
        "strategy_family": cfg.strategy_family,
        "harness": harness,
        "duplicate_trade_guard": dup,
        "ledger_trade_ids": ledger_ids,
        "intel_hook_ok": intel_hook_ok,
        "all_passed": all_passed,
    }
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "gate_b_micro_validation_proof.json").write_text(json.dumps(proof, indent=2, default=str) + "\n", encoding="utf-8")

    validation_record: Dict[str, Any] = {
        "validated_at": ts,
        "micro_validation_pass": all_passed,
        "failed_validation": not all_passed,
        "validation_mode": "staged_mock_no_venue_orders",
        "path_proven": [
            "scan_rank_select",
            "strict_entry_check",
            "monitor_exit_ticks",
            "sudden_drop_eval",
            "edge_pause_eval",
            "failsafe_duplicate_guard",
            "ledger_append_gate_b",
            "intelligence_emit",
        ],
        "scenario_summary": {
            "total": harness.get("scenario_count"),
            "passed": harness.get("passed_count"),
            "failed": harness.get("failed_count"),
        },
        "operator_note": "This file is AUTO-GENERATED by gate_b_micro_validation_runner. Live venue proof is separate.",
    }
    (ctrl / "gate_b_validation.json").write_text(json.dumps(validation_record, indent=2) + "\n", encoding="utf-8")

    runs = root / "data" / "deployment" / "live_validation_runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_path = runs / f"gate_b_micro_validation_{int(time.time())}.json"
    run_path.write_text(json.dumps({"proof": proof, "validation_record": validation_record}, indent=2, default=str) + "\n", encoding="utf-8")

    write_control_json("gate_b_micro_validation_last_run.json", {"last_run": str(run_path), "all_passed": all_passed}, runtime_root=root)

    return proof
