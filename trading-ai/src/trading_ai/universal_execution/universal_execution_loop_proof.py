"""
Persistent proof for buy → sell → log → rebuy gating — maps contract stages to named lifecycle flags.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.universal_execution.rebuy_policy import TerminalHonestState, can_open_next_trade_after


class ExecutionLifecycleState(str, Enum):
    """Hard runtime states — rebuy only after FINALIZED or explicit safe terminal (see rebuy_policy)."""

    IN_FLIGHT = "IN_FLIGHT"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    FINALIZED = "FINALIZED"


def _stage_ok(contract_dict: Dict[str, Any], name: str) -> bool:
    s = contract_dict.get(name) or {}
    return bool(isinstance(s, dict) and s.get("ok"))


def classify_execution_lifecycle_state(
    *,
    cycle_ok: bool,
    final_execution_proven: bool,
    terminal_honest_state: Optional[str],
) -> ExecutionLifecycleState:
    t = str(terminal_honest_state or "").strip()
    if final_execution_proven and cycle_ok:
        return ExecutionLifecycleState.FINALIZED
    if t == TerminalHonestState.UNRESOLVED_IN_FLIGHT.value:
        return ExecutionLifecycleState.IN_FLIGHT
    if t in (
        TerminalHonestState.ENTRY_FAILED_PRE_FILL.value,
        TerminalHonestState.VENUE_REJECTED.value,
        TerminalHonestState.DUPLICATE_BLOCKED.value,
        TerminalHonestState.ADAPTIVE_BRAKE_BLOCKED.value,
        TerminalHonestState.GOVERNANCE_BLOCKED.value,
    ):
        return ExecutionLifecycleState.TERMINAL_FAILURE
    if t in (TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE.value, TerminalHonestState.ENTRY_FILLED_EXIT_FAILED.value):
        return ExecutionLifecycleState.PARTIAL_FAILURE
    if t == TerminalHonestState.ROUND_TRIP_SUCCESS.value:
        return ExecutionLifecycleState.FINALIZED
    if not cycle_ok and not t:
        return ExecutionLifecycleState.PARTIAL_FAILURE
    return ExecutionLifecycleState.IN_FLIGHT


def build_universal_execution_loop_proof_payload(
    result: Dict[str, Any],
    *,
    partial_failure_flags: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """
    Flatten execution_truth_contract + universal_proof into the operator-facing lifecycle map.

    Sequential contract (all must be true for rebuy when ``final_execution_proven``):
    entry_intent → entry_fill → exit_intent → exit_fill → pnl → local → remote (if required) →
    governance → review → rebuy eligibility.
    """
    contract = result.get("execution_truth_contract") or {}
    if isinstance(contract, dict) and "stages" not in contract:
        stages = contract
    else:
        stages = (contract.get("stages") or contract) if isinstance(contract, dict) else {}

    bundle = result.get("bundle") or {}
    univ = bundle.get("universal_proof") or {}
    remote_diag = bundle.get("remote_write") or {}
    remote_required = bool(remote_diag.get("remote_required", True))

    entry_intent = _stage_ok(stages, "STAGE_2_ENTRY_ORDER_SUBMITTED")
    entry_fill = _stage_ok(stages, "STAGE_3_ENTRY_FILL_CONFIRMED")
    exit_intent = _stage_ok(stages, "STAGE_4_EXIT_ORDER_SUBMITTED")
    exit_fill = _stage_ok(stages, "STAGE_5_EXIT_FILL_CONFIRMED")
    pnl_ok = _stage_ok(stages, "STAGE_6_PNL_VERIFIED")
    local_ok = _stage_ok(stages, "STAGE_7_LOCAL_DATA_WRITTEN")
    remote_ok = _stage_ok(stages, "STAGE_8_REMOTE_DATA_WRITTEN") or (not remote_required)
    gov_ok = _stage_ok(stages, "STAGE_9_GOVERNANCE_LOGGED")
    review_ok = _stage_ok(stages, "STAGE_10_REVIEW_ARTIFACTS_UPDATED")

    flags = list(partial_failure_flags or [])
    if bundle.get("partial_failure_flags"):
        flags.extend([str(x) for x in bundle.get("partial_failure_flags") or []])

    base_proven = bool(univ.get("final_execution_proven")) and bool(result.get("final_execution_proven"))
    if flags:
        base_proven = False

    lifecycle: Dict[str, Any] = {
        "entry_intent": entry_intent,
        "entry_fill_confirmed": entry_fill,
        "exit_intent": exit_intent,
        "exit_fill_confirmed": exit_fill,
        "pnl_verified": pnl_ok,
        "local_write_ok": local_ok,
        "remote_write_ok": remote_ok,
        "remote_write_required": remote_required,
        "governance_logged": gov_ok,
        "review_update_ok": review_ok,
    }

    term = str(result.get("terminal_honest_state") or "")
    ls = classify_execution_lifecycle_state(
        cycle_ok=bool(result.get("cycle_ok")),
        final_execution_proven=base_proven,
        terminal_honest_state=term or None,
    )

    contract_stages = result.get("execution_truth_contract") if isinstance(result.get("execution_truth_contract"), dict) else {}
    prior_summary: Dict[str, Any] = {
        "final_execution_proven": base_proven,
        "terminal_honest_state": term,
        "entry_fill_confirmed": entry_fill,
        "exit_fill_confirmed": exit_fill,
        "pnl_verified": pnl_ok,
        "local_write_ok": local_ok,
        "stages": contract_stages,
    }
    rebuy_ok, rebuy_why = can_open_next_trade_after(prior_summary)

    blocking: Optional[str] = None
    if not base_proven:
        blocking = univ.get("blocking_reason") or result.get("failure_code") or "lifecycle_incomplete"
    if flags:
        blocking = (blocking or "") + "|partial_failure_flags:" + ",".join(flags)
    if not rebuy_ok:
        blocking = blocking or rebuy_why

    ready_rebuy = bool(rebuy_ok and not flags)

    return {
        "truth_version": "universal_execution_loop_proof_v1",
        "last_trade_id": result.get("trade_id") or bundle.get("trade_id"),
        "lifecycle_stages": lifecycle,
        "execution_lifecycle_state": ls.value,
        "partial_failure_flags": flags,
        "final_execution_proven": base_proven,
        "ready_for_rebuy": ready_rebuy,
        "blocking_reason_if_any": blocking,
        "rebuy_policy_reason": rebuy_why,
        "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": bool(base_proven),
    }


def write_universal_execution_loop_proof(
    payload: Dict[str, Any],
    *,
    runtime_root: Any = None,
) -> Dict[str, Any]:
    from trading_ai.runtime_paths import ezras_runtime_root
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    rel = "data/control/universal_execution_loop_proof.json"
    ad.write_json(rel, payload)
    return {"written": True, "path": rel}


def repair_universal_execution_loop_proof_if_inconsistent(*, runtime_root: Any = None) -> Dict[str, Any]:
    """
    Safety-tightening repair only.

    If the persisted loop proof claims ``final_execution_proven=true`` but lifecycle stages are not all
    satisfied, rewrite the proof to a conservative "not proven" state so rebuy gating can behave
    truthfully (no phantom proven success).
    """
    from trading_ai.runtime_paths import ezras_runtime_root
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    rel = "data/control/universal_execution_loop_proof.json"
    cur = ad.read_json(rel) or {}
    if not isinstance(cur, dict):
        return {"repaired": False, "reason": "loop_proof_not_a_dict"}
    if cur.get("final_execution_proven") is not True:
        return {"repaired": False, "reason": "final_execution_proven_not_true"}
    ls = cur.get("lifecycle_stages") if isinstance(cur.get("lifecycle_stages"), dict) else {}
    ls = ls if isinstance(ls, dict) else {}
    required = ("entry_fill_confirmed", "exit_fill_confirmed", "pnl_verified", "local_write_ok")
    if all(bool(ls.get(k)) for k in required):
        return {"repaired": False, "reason": "stage_flags_consistent"}

    repaired = dict(cur)
    repaired["final_execution_proven"] = False
    repaired["BUY_SELL_LOG_REBUY_RUNTIME_PROVEN"] = False
    repaired["ready_for_rebuy"] = False
    repaired["blocking_reason_if_any"] = "repaired_inconsistent_final_execution_proven_flag"
    repaired["rebuy_policy_reason"] = "repaired_inconsistent_final_execution_proven_flag"
    repaired.setdefault("partial_failure_flags", [])
    if isinstance(repaired["partial_failure_flags"], list):
        if "loop_proof_repaired_inconsistent_final_execution_proven" not in repaired["partial_failure_flags"]:
            repaired["partial_failure_flags"].append("loop_proof_repaired_inconsistent_final_execution_proven")
    # Preserve lifecycle_stages as-is (they are the evidence of incompleteness).
    ad.write_json(rel, repaired)
    return {"repaired": True, "reason": "inconsistent_final_execution_proven_and_stage_flags", "path": rel}


def write_loop_proof_from_trade_result(
    result: Dict[str, Any],
    *,
    runtime_root: Any = None,
    partial_failure_flags: Optional[list[str]] = None,
) -> Dict[str, Any]:
    payload = build_universal_execution_loop_proof_payload(result, partial_failure_flags=partial_failure_flags)
    meta = write_universal_execution_loop_proof(payload, runtime_root=runtime_root)
    try:
        from trading_ai.first_20.integration import on_universal_loop_proof_written

        on_universal_loop_proof_written(payload, runtime_root=runtime_root)
    except Exception:
        pass
    try:
        from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle

        write_live_switch_closure_bundle(
            runtime_root=runtime_root,
            trigger_surface="universal_loop_proof",
            reason="universal_execution_loop_proof_write",
        )
    except Exception:
        pass
    return {"loop_proof": payload, **meta}
