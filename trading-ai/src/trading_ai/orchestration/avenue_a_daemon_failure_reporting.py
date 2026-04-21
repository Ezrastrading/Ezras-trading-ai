"""
Operator-grade failure envelopes for Avenue A daemon cycles + persistent failure truth artifact.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter

_FAILURE_TRUTH_JSON = "data/control/avenue_a_daemon_failure_truth.json"
_FAILURE_TRUTH_TXT = "data/control/avenue_a_daemon_failure_truth.txt"


def build_daemon_cycle_terminal_fields(
    proof: Dict[str, Any],
    universal_emit: Dict[str, Any],
    *,
    runtime_root: Path,
    daemon_mode: str,
    ts: str,
    product_id_arg: str,
) -> Dict[str, Any]:
    """
    Flat fields required on failed (and successful) daemon cycles for JSON artifacts.
    ``failure_reason`` is never null when the cycle is not fully successful (caller must pass proof with attach_terminal_failure applied).
    """
    pid = proof.get("product_id") or proof.get("venue_product_id") or product_id_arg
    fr = proof.get("failure_reason")
    if fr is None and not (proof.get("execution_success") and proof.get("FINAL_EXECUTION_PROVEN")):
        fr = proof.get("error")
    return {
        "failure_stage": proof.get("failure_stage"),
        "failure_code": proof.get("failure_code"),
        "failure_reason": fr,
        "final_execution_proven": proof.get("final_execution_proven"),
        "universal_loop_emit_reason": universal_emit.get("reason"),
        "universal_loop_emit_blocking_condition": universal_emit.get("blocking_condition"),
        "universal_loop_emit_proof_fields_missing_or_false": universal_emit.get("proof_fields_missing_or_false"),
        "trade_id": proof.get("trade_id"),
        "product_id": pid,
        "order_id_buy": proof.get("order_id_buy"),
        "order_id_sell": proof.get("order_id_sell"),
        "runtime_root": str(runtime_root),
        "daemon_mode": daemon_mode,
        "ts": ts,
    }


def _safe_to_retry_immediately(failure_code: Optional[str]) -> bool:
    if not failure_code:
        return True
    if failure_code == "buy_blocked_duplicate_guard":
        return False
    if failure_code in ("proof_contract_not_satisfied", "review_update_failed"):
        return True
    return True


def _next_operator_action(failure_code: Optional[str], *, runtime_root: Path) -> str:
    if not failure_code:
        return (
            f"No failure recorded — inspect data/control/runtime_runner_last_cycle.json under EZRAS_RUNTIME_ROOT={runtime_root}"
        )
    if failure_code == "buy_blocked_duplicate_guard":
        try:
            ad = LocalStorageAdapter(runtime_root=runtime_root)
            fs = ad.read_json("data/control/failsafe_status.json") or {}
            blk = fs.get("duplicate_guard_last_block") if isinstance(fs.get("duplicate_guard_last_block"), dict) else {}
            rem = blk.get("cooldown_remaining_sec")
            key = blk.get("key")
            tid = blk.get("trigger_trade_id")
            if rem is not None:
                return (
                    f"Duplicate window active. Cooldown remaining ~{rem}s (key={key}, trigger_trade_id={tid}). "
                    "Wait until cooldown is 0, then re-run a daemon cycle."
                )
        except Exception:
            pass
        return (
            "Wait for EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC to elapse or inspect data/control/failsafe_status.json recent_orders; "
            "then re-run a daemon cycle when duplicate window is clear."
        )
    if failure_code == "supabase_sync_failed":
        return "Check SUPABASE connectivity and data/control supabase sync diagnostics; fix and retry."
    if failure_code == "databank_write_failed":
        return "Inspect local_write_diagnostics / databank process logs; fix local write path; retry."
    if failure_code == "sell_fill_not_confirmed" or failure_code == "sell_order_submit_failed":
        return "Inspect sell_leg_diagnostics and Coinbase order state; may require manual flatten if position left open."
    return (
        f"Inspect execution_proof/live_execution_validation.json and failure_code={failure_code}; "
        f"address root cause then: EZRAS_AVENUE_A_DAEMON_MODE=supervised_live avenue A daemon cycle."
    )


def write_avenue_a_daemon_failure_truth(
    *,
    runtime_root: Path,
    terminal: Dict[str, Any],
    success: bool,
    skipped: bool,
) -> None:
    """Append-oriented truth: last failure summary + streak of same failure_code."""
    if success or skipped:
        return
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    st = ad.read_json("data/control/avenue_a_daemon_state.json") or {}
    code = terminal.get("failure_code")
    prev = st.get("last_recorded_daemon_failure_code")
    streak = int(st.get("consecutive_same_daemon_failure_code") or 0)
    if code and code == prev:
        streak += 1
    else:
        streak = 1
    st["last_recorded_daemon_failure_code"] = code
    st["consecutive_same_daemon_failure_code"] = streak
    ad.write_json("data/control/avenue_a_daemon_state.json", st)

    payload = {
        "truth_version": "avenue_a_daemon_failure_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_failure_code": code,
        "last_failure_reason": terminal.get("failure_reason"),
        "last_failure_stage": terminal.get("failure_stage"),
        "repeated_same_failure_count": streak,
        "safe_to_retry_immediately": _safe_to_retry_immediately(str(code) if code else None),
        "next_command_or_action": _next_operator_action(str(code) if code else None, runtime_root=runtime_root),
        "runtime_root": str(runtime_root),
        "honesty": "Derived from live_validation terminal fields; does not fabricate proof.",
    }
    ad.write_json(_FAILURE_TRUTH_JSON, payload)
    ad.write_text(_FAILURE_TRUTH_TXT, json.dumps(payload, indent=2) + "\n")
