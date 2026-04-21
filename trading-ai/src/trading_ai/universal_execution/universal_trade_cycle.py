"""
Universal round-trip lifecycle — prerequisite stage enforcement; adapter-driven venue logic.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.universal_execution.execution_truth_contract import ExecutionTruthContract, ExecutionTruthStage
from trading_ai.universal_execution.rebuy_policy import TerminalHonestState
from trading_ai.universal_execution.universal_execution_loop_proof import write_loop_proof_from_trade_result
from trading_ai.universal_execution.universal_execution_proof import build_universal_execution_proof_payload


def execute_round_trip_with_truth(
    adapter: Any,
    *,
    ctx: Any,
    scan_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run SCAN → … → READY. Stops at first honest failure; never marks later stages without prerequisites.

    ``adapter`` must implement :class:`AvenueAdapterBase` (or compatible methods).
    """
    from trading_ai.universal_execution.avenue_adapter import AdapterContext, AvenueAdapterBase

    contract = ExecutionTruthContract()
    if not isinstance(adapter, AvenueAdapterBase):
        bad = {
            "cycle_ok": False,
            "terminal_honest_state": TerminalHonestState.UNRESOLVED_IN_FLIGHT.value,
            "error": "adapter_must_subclass_AvenueAdapterBase",
            "execution_truth_contract": contract.to_dict(),
            "bundle": {},
        }
        _persist_universal_loop_proof(bad, None)
        return bad

    trade_id = f"univ_{uuid.uuid4().hex[:16]}"
    bundle: Dict[str, Any] = {
        "trade_id": trade_id,
        "truth_version": "universal_trade_cycle_v1",
        "adapter": type(adapter).__name__,
        "avenue_id": getattr(adapter, "avenue_id", ""),
    }
    actx = ctx if isinstance(ctx, AdapterContext) else AdapterContext(avenue_id=str(getattr(adapter, "avenue_id", "")))

    gaps = adapter.capability_gaps()
    if any(g.blocks_live_orders for g in gaps):
        gap_out = {
            "cycle_ok": False,
            "trade_id": trade_id,
            "execution_truth_contract": contract.to_dict(),
            "terminal_honest_state": TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE.value,
            "capability_gaps": [{"code": g.code, "detail": g.detail} for g in gaps],
            "honesty": "Universal cycle did not start — adapter reports blocking capability gaps.",
            "bundle": bundle,
        }
        _persist_universal_loop_proof(gap_out, actx)
        return gap_out

    # 0 — candidate
    cands, scan_diag = adapter.scan_candidates(actx)
    chosen, sel_diag = adapter.select_candidate(actx, cands)
    bundle["scan_diagnostics"] = {"scan": scan_diag, "select": sel_diag}
    if not chosen:
        contract.set_stage(
            ExecutionTruthStage.STAGE_0_CANDIDATE_SELECTED,
            ok=False,
            avenue_id=actx.avenue_id,
            gate_id=actx.gate_id,
            strategy_id=actx.strategy_id,
            route=actx.route,
            execution_profile=actx.execution_profile,
            trade_id=trade_id,
            blocking_reason="no_candidate_selected",
            proof_source="adapter.select_candidate",
            proof_kind="scan_select",
        )
        return _finalize_failure(
            contract,
            bundle,
            TerminalHonestState.ENTRY_FAILED_PRE_FILL,
            "no_candidate",
            actx,
        )

    contract.set_stage(
        ExecutionTruthStage.STAGE_0_CANDIDATE_SELECTED,
        ok=True,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        proof_source="adapter.select_candidate",
        proof_kind="scan_select",
    )

    ok_pre, pre_diag = adapter.pretrade_validate(actx, chosen)
    bundle["pretrade_diagnostics"] = pre_diag
    if not ok_pre:
        contract.set_stage(
            ExecutionTruthStage.STAGE_1_PRETRADE_GUARDS_PASSED,
            ok=False,
            avenue_id=actx.avenue_id,
            gate_id=actx.gate_id,
            strategy_id=actx.strategy_id,
            route=actx.route,
            execution_profile=actx.execution_profile,
            trade_id=trade_id,
            blocking_reason=str(pre_diag.get("blocking_reason") or "pretrade_failed"),
            proof_source="adapter.pretrade_validate",
            proof_kind="pretrade",
        )
        br = str(pre_diag.get("blocking_reason") or "").lower()
        if "duplicate" in br:
            pre_term = TerminalHonestState.DUPLICATE_BLOCKED
        elif "governance" in str(pre_diag).lower():
            pre_term = TerminalHonestState.GOVERNANCE_BLOCKED
        else:
            pre_term = TerminalHonestState.ADAPTIVE_BRAKE_BLOCKED
        return _finalize_failure(contract, bundle, pre_term, "pretrade_blocked", actx)

    contract.set_stage(
        ExecutionTruthStage.STAGE_1_PRETRADE_GUARDS_PASSED,
        ok=True,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        proof_source="adapter.pretrade_validate",
        proof_kind="pretrade",
    )

    ok_ent, ent_meta = adapter.submit_entry(actx, chosen)
    bundle["entry_submit"] = ent_meta
    if not ok_ent:
        contract.set_stage(
            ExecutionTruthStage.STAGE_2_ENTRY_ORDER_SUBMITTED,
            ok=False,
            avenue_id=actx.avenue_id,
            gate_id=actx.gate_id,
            strategy_id=actx.strategy_id,
            route=actx.route,
            execution_profile=actx.execution_profile,
            trade_id=trade_id,
            blocking_reason=str(ent_meta.get("reason") or "entry_submit_failed"),
            proof_source="adapter.submit_entry",
            proof_kind="order_ack",
        )
        return _finalize_failure(contract, bundle, TerminalHonestState.VENUE_REJECTED, "entry_submit_failed", actx)

    contract.set_stage(
        ExecutionTruthStage.STAGE_2_ENTRY_ORDER_SUBMITTED,
        ok=True,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        proof_source=str(ent_meta.get("proof_source") or "adapter.submit_entry"),
        proof_kind=str(ent_meta.get("proof_kind") or "order_ack"),
    )

    ok_ef, ef_diag = adapter.confirm_entry_fill(actx, ent_meta)
    bundle["entry_fill"] = ef_diag
    if not ok_ef:
        contract.set_stage(
            ExecutionTruthStage.STAGE_3_ENTRY_FILL_CONFIRMED,
            ok=False,
            avenue_id=actx.avenue_id,
            gate_id=actx.gate_id,
            strategy_id=actx.strategy_id,
            route=actx.route,
            execution_profile=actx.execution_profile,
            trade_id=trade_id,
            blocking_reason=str(ef_diag.get("blocking_reason") or "entry_fill_not_confirmed"),
            proof_source="adapter.confirm_entry_fill",
            proof_kind="fills_or_snapshot",
        )
        return _finalize_failure(contract, bundle, TerminalHonestState.ENTRY_FAILED_PRE_FILL, "entry_fill_failed", actx)

    contract.set_stage(
        ExecutionTruthStage.STAGE_3_ENTRY_FILL_CONFIRMED,
        ok=True,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        proof_source=str(ef_diag.get("truth_source") or "adapter.confirm_entry_fill"),
        proof_kind=str(ef_diag.get("proof_kind") or "fills"),
    )

    exit_plan, ep_diag = adapter.compute_exit_plan(actx, {**ent_meta, **ef_diag})
    bundle["exit_plan"] = {**exit_plan, "diagnostics": ep_diag}

    ok_x, x_meta = adapter.submit_exit(actx, exit_plan)
    bundle["exit_submit"] = x_meta
    if not ok_x:
        contract.set_stage(
            ExecutionTruthStage.STAGE_4_EXIT_ORDER_SUBMITTED,
            ok=False,
            avenue_id=actx.avenue_id,
            gate_id=actx.gate_id,
            strategy_id=actx.strategy_id,
            route=actx.route,
            execution_profile=actx.execution_profile,
            trade_id=trade_id,
            blocking_reason=str(x_meta.get("reason") or "exit_submit_failed"),
            proof_source="adapter.submit_exit",
            proof_kind="order_ack",
        )
        return _finalize_failure(contract, bundle, TerminalHonestState.ENTRY_FILLED_EXIT_FAILED, "exit_submit_failed", actx)

    contract.set_stage(
        ExecutionTruthStage.STAGE_4_EXIT_ORDER_SUBMITTED,
        ok=True,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        proof_source=str(x_meta.get("proof_source") or "adapter.submit_exit"),
        proof_kind=str(x_meta.get("proof_kind") or "order_ack"),
    )

    ok_xf, xf_diag = adapter.confirm_exit_fill(actx, {**x_meta, **ent_meta})
    bundle["exit_fill"] = xf_diag
    if not ok_xf:
        contract.set_stage(
            ExecutionTruthStage.STAGE_5_EXIT_FILL_CONFIRMED,
            ok=False,
            avenue_id=actx.avenue_id,
            gate_id=actx.gate_id,
            strategy_id=actx.strategy_id,
            route=actx.route,
            execution_profile=actx.execution_profile,
            trade_id=trade_id,
            blocking_reason=str(xf_diag.get("blocking_reason") or "exit_fill_not_confirmed"),
            proof_source="adapter.confirm_exit_fill",
            proof_kind="fills_or_snapshot",
        )
        return _finalize_failure(contract, bundle, TerminalHonestState.ENTRY_FILLED_EXIT_FAILED, "exit_fill_failed", actx)

    contract.set_stage(
        ExecutionTruthStage.STAGE_5_EXIT_FILL_CONFIRMED,
        ok=True,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        proof_source=str(xf_diag.get("truth_source") or "adapter.confirm_exit_fill"),
        proof_kind=str(xf_diag.get("proof_kind") or "fills"),
    )

    pnl_block, pnl_diag = adapter.compute_realized_pnl(actx, {**ent_meta, **ef_diag}, {**x_meta, **xf_diag})
    bundle["pnl"] = {**pnl_block, "diagnostics": pnl_diag}
    ok_pnl = bool(pnl_block.get("complete")) if "complete" in pnl_block else bool(pnl_block.get("net_pnl") is not None)
    if not ok_pnl:
        contract.set_stage(
            ExecutionTruthStage.STAGE_6_PNL_VERIFIED,
            ok=False,
            avenue_id=actx.avenue_id,
            gate_id=actx.gate_id,
            strategy_id=actx.strategy_id,
            route=actx.route,
            execution_profile=actx.execution_profile,
            trade_id=trade_id,
            blocking_reason="pnl_incomplete",
            proof_source="adapter.compute_realized_pnl",
            proof_kind="pnl_math",
        )
        return _finalize_failure(contract, bundle, TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE, "pnl_incomplete", actx)

    contract.set_stage(
        ExecutionTruthStage.STAGE_6_PNL_VERIFIED,
        ok=True,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        proof_source="adapter.compute_realized_pnl",
        proof_kind="pnl_math",
    )

    record = adapter.build_trade_record(
        actx, entry_meta={**ent_meta, **ef_diag}, exit_meta={**x_meta, **xf_diag}, pnl_block=pnl_block
    )
    bundle["normalized_trade_record"] = record

    loc_ok, loc_diag = adapter.append_local_trade_event(actx, record)
    bundle["local_write"] = loc_diag
    contract.set_stage(
        ExecutionTruthStage.STAGE_7_LOCAL_DATA_WRITTEN,
        ok=bool(loc_ok),
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        blocking_reason=None if loc_ok else "local_write_failed",
        proof_source="adapter.append_local_trade_event",
        proof_kind="databank_append",
    )
    if not loc_ok:
        return _finalize_failure(contract, bundle, TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE, "local_write_failed", actx)

    rem_ok, rem_diag = adapter.upsert_remote_trade_event(actx, record)
    bundle["remote_write"] = rem_diag
    remote_required = bool(rem_diag.get("remote_required", True))
    contract.set_stage(
        ExecutionTruthStage.STAGE_8_REMOTE_DATA_WRITTEN,
        ok=bool(rem_ok) or not remote_required,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        blocking_reason=None if (rem_ok or not remote_required) else "remote_write_failed",
        proof_source="adapter.upsert_remote_trade_event",
        proof_kind="supabase_or_remote",
    )
    if remote_required and not rem_ok:
        return _finalize_failure(contract, bundle, TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE, "remote_write_failed", actx)

    gov_ok, gov_diag = adapter.governance_log(actx, record)
    bundle["governance"] = gov_diag
    contract.set_stage(
        ExecutionTruthStage.STAGE_9_GOVERNANCE_LOGGED,
        ok=bool(gov_ok),
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        blocking_reason=None if gov_ok else "governance_log_failed",
        proof_source="governance",
        proof_kind="governance_log",
    )
    if not gov_ok:
        return _finalize_failure(contract, bundle, TerminalHonestState.GOVERNANCE_BLOCKED, "governance_failed", actx)

    summ_ok, summ_diag = adapter.refresh_summaries(actx, record)
    bundle["review_refresh"] = summ_diag
    contract.set_stage(
        ExecutionTruthStage.STAGE_10_REVIEW_ARTIFACTS_UPDATED,
        ok=bool(summ_ok),
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        blocking_reason=None if summ_ok else "review_refresh_failed",
        proof_source="adapter.refresh_summaries",
        proof_kind="operator_artifacts",
    )
    if not summ_ok:
        return _finalize_failure(contract, bundle, TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE, "review_refresh_failed", actx)

    proof = adapter.produce_execution_proof(actx, bundle)
    bundle["adapter_proof"] = proof

    final_proof = build_universal_execution_proof_payload(bundle, contract)
    bundle["universal_proof"] = final_proof

    # Evidence-first truth validation: require mandatory artifacts on disk.
    ready = bool(final_proof.get("final_execution_proven"))
    try:
        rr = None
        if isinstance(actx, AdapterContext):
            rr = (actx.extra or {}).get("runtime_root")
        if rr:
            from trading_ai.truth_engine import truth_chain_for_post_trade, validate_truth_chain

            t = validate_truth_chain(truth_chain_for_post_trade(runtime_root=Path(str(rr))))
            bundle["truth_chain"] = t
            try:
                from trading_ai.storage.storage_adapter import LocalStorageAdapter

                LocalStorageAdapter(runtime_root=Path(str(rr))).write_json(
                    "data/control/truth_chain_last.json", t
                )
            except Exception:
                pass
            ready = bool(ready) and bool(t.get("ok"))
        else:
            bundle["truth_chain"] = {"ok": False, "error": "missing_runtime_root_for_truth_validation"}
            ready = False
    except Exception as exc:
        bundle["truth_chain"] = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
        ready = False
    contract.set_stage(
        ExecutionTruthStage.STAGE_11_READY_FOR_NEXT_CYCLE,
        ok=ready,
        avenue_id=actx.avenue_id,
        gate_id=actx.gate_id,
        strategy_id=actx.strategy_id,
        route=actx.route,
        execution_profile=actx.execution_profile,
        trade_id=trade_id,
        blocking_reason=None if ready else "final_proof_not_complete",
        proof_source="build_universal_execution_proof_payload",
        proof_kind="universal_proof",
    )
    final_proof["ready_for_next_cycle"] = ready

    out_ok = {
        "cycle_ok": ready,
        "trade_id": trade_id,
        "terminal_honest_state": TerminalHonestState.ROUND_TRIP_SUCCESS.value if ready else TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE.value,
        "execution_truth_contract": contract.to_dict(),
        "bundle": bundle,
        "final_execution_proven": ready,
    }
    _persist_universal_loop_proof(out_ok, actx)
    return out_ok


def _persist_universal_loop_proof(result: Dict[str, Any], actx: Any) -> None:
    rr = None
    try:
        if isinstance(actx, AdapterContext):
            rr = (actx.extra or {}).get("runtime_root")
    except Exception:
        rr = None
    try:
        write_loop_proof_from_trade_result(result, runtime_root=rr)
    except Exception:
        pass


def _finalize_failure(
    contract: ExecutionTruthContract,
    bundle: Dict[str, Any],
    terminal: TerminalHonestState,
    code: str,
    actx: Optional[Any] = None,
) -> Dict[str, Any]:
    bundle["failure_code"] = code
    out = {
        "cycle_ok": False,
        "trade_id": bundle.get("trade_id"),
        "terminal_honest_state": terminal.value,
        "execution_truth_contract": contract.to_dict(),
        "bundle": bundle,
        "final_execution_proven": False,
        "failure_code": code,
    }
    _persist_universal_loop_proof(out, actx)
    return out


def run_universal_trade_cycle(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Alias for :func:`execute_round_trip_with_truth`."""
    return execute_round_trip_with_truth(*args, **kwargs)
