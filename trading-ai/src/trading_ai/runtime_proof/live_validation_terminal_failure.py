"""
Stable terminal failure codes and human reasons for Gate A live execution validation.

Every failed cycle must expose failure_code, failure_stage, failure_reason (never null when not successful).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# --- Stable taxonomy (Avenue A / supervised daemon) ---------------------------------

FAILURE_CODE_BUY_BLOCKED_DUPLICATE_GUARD = "buy_blocked_duplicate_guard"
FAILURE_CODE_BUY_BLOCKED_GOVERNANCE = "buy_blocked_governance"
FAILURE_CODE_BUY_ORDER_SUBMIT_FAILED = "buy_order_submit_failed"
FAILURE_CODE_BUY_FILL_NOT_CONFIRMED = "buy_fill_not_confirmed"
FAILURE_CODE_SELL_ORDER_SUBMIT_FAILED = "sell_order_submit_failed"
FAILURE_CODE_SELL_FILL_NOT_CONFIRMED = "sell_fill_not_confirmed"
FAILURE_CODE_PNL_VERIFICATION_FAILED = "pnl_verification_failed"
FAILURE_CODE_DATABANK_WRITE_FAILED = "databank_write_failed"
FAILURE_CODE_SUPABASE_SYNC_FAILED = "supabase_sync_failed"
FAILURE_CODE_REVIEW_UPDATE_FAILED = "review_update_failed"
FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED = "proof_contract_not_satisfied"
FAILURE_CODE_COINBASE_CREDENTIALS_NOT_CONFIGURED = "coinbase_credentials_not_configured"
FAILURE_CODE_COINBASE_UNAUTHORIZED = "coinbase_401_unauthorized"
FAILURE_CODE_UNKNOWN_TERMINAL_FAILURE = "unknown_terminal_failure"

FAILURE_STAGE_PRE_BUY = "pre_buy"
FAILURE_STAGE_BUY = "buy"
FAILURE_STAGE_SELL = "sell"
FAILURE_STAGE_POST_TRADE_PIPELINE = "post_trade_pipeline"
FAILURE_STAGE_PROOF = "proof"
FAILURE_STAGE_SYSTEM = "system"


def _join_reasons(parts: List[str]) -> str:
    return "; ".join(p for p in parts if p)


_LIVE_CONFIRM_ENV = "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM"
_LIVE_CONFIRM_VAL = "YES_I_UNDERSTAND_REAL_CAPITAL"


def classify_early_guard_failure(error: str) -> Tuple[str, str, str]:
    """
    Map ``base_out['error']`` strings from early returns (pre-trade) to taxonomy.
    Returns (failure_code, failure_stage, failure_reason).
    """
    e = str(error or "").strip()
    low = e.lower()
    if ("duplicate" in low or "duplicate_trade" in low) and (
        "failsafe" in low or "duplicate_trade_guard" in low
    ):
        return (
            FAILURE_CODE_BUY_BLOCKED_DUPLICATE_GUARD,
            FAILURE_STAGE_PRE_BUY,
            e or "duplicate_trade_guard_blocked_buy",
        )
    if "governance_blocked" in low:
        return FAILURE_CODE_BUY_BLOCKED_GOVERNANCE, FAILURE_STAGE_PRE_BUY, e
    if "coinbase_auth_failure:" in low or (
        "coinbase credentials not configured" in low and "quote_precheck" not in low
    ):
        if "401" in low or "unauthorized" in low:
            return FAILURE_CODE_COINBASE_UNAUTHORIZED, FAILURE_STAGE_PRE_BUY, e
        return FAILURE_CODE_COINBASE_CREDENTIALS_NOT_CONFIGURED, FAILURE_STAGE_PRE_BUY, e
    if "missing_or_invalid" in low and (
        "live_single" in low or "validation_confirm" in low or "live_execution_validation_confirm" in low
    ):
        return (
            FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED,
            FAILURE_STAGE_PRE_BUY,
            _join_reasons(
                [
                    "Supervised Gate A live validation requires explicit operator acknowledgement (not autonomous enablement).",
                    f"Set {_LIVE_CONFIRM_ENV}={_LIVE_CONFIRM_VAL} in this shell, e.g.",
                    f'export {_LIVE_CONFIRM_ENV}="{_LIVE_CONFIRM_VAL}"',
                    "When EZRAS_AVENUE_A_DAEMON_ACTIVE=1, alternate: data/control/avenue_a_autonomous_live_ack.json "
                    "with confirmed true under allowed scope (see live_execution_validation).",
                    e or "operator_intent_gate_not_satisfied",
                ]
            ),
        )
    if "coinbase_live_execution_not_enabled" in low:
        return FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED, FAILURE_STAGE_PRE_BUY, e
    if "ezras_dry_run" in low:
        return FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED, FAILURE_STAGE_PRE_BUY, e
    if "system_execution_lock" in low:
        return FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED, FAILURE_STAGE_PRE_BUY, e
    if "halted" in low or "assert_system_not_halted" in low:
        return FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED, FAILURE_STAGE_SYSTEM, e
    if "quote_precheck_failed" in low:
        return FAILURE_CODE_BUY_ORDER_SUBMIT_FAILED, FAILURE_STAGE_PRE_BUY, e
    if "buy_failed" in low:
        return FAILURE_CODE_BUY_ORDER_SUBMIT_FAILED, FAILURE_STAGE_BUY, e
    if "buy_not_filled" in low:
        return FAILURE_CODE_BUY_FILL_NOT_CONFIRMED, FAILURE_STAGE_BUY, e
    if "flatten" in low or "flatten_size" in low or "base_qty" in low:
        return FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED, FAILURE_STAGE_SELL, e
    return FAILURE_CODE_UNKNOWN_TERMINAL_FAILURE, FAILURE_STAGE_SYSTEM, e or "unspecified_early_failure"


def _list_proof_contract_gaps(base_out: Dict[str, Any]) -> List[str]:
    """Which booleans block FINAL_EXECUTION_PROVEN (same contract as all_ok in live_execution_validation)."""
    gaps: List[str] = []
    if not base_out.get("execution_success"):
        gaps.append("execution_success_false")
    if not base_out.get("coinbase_order_verified"):
        gaps.append("coinbase_order_verified_false")
    if not base_out.get("databank_written"):
        gaps.append("databank_written_false")
    if not base_out.get("supabase_synced"):
        gaps.append("supabase_synced_false")
    if not base_out.get("governance_logged"):
        gaps.append("governance_logged_false")
    if not base_out.get("packet_updated"):
        gaps.append("packet_updated_false")
    if not base_out.get("scheduler_stable"):
        gaps.append("scheduler_stable_false")
    return gaps


def classify_post_trade_pipeline_failure(base_out: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    After buy/sell legs ran — execution_success / FINAL may fail on pipeline or proof booleans.
    """
    exec_ok = bool(base_out.get("execution_success"))
    final_ok = bool(base_out.get("FINAL_EXECUTION_PROVEN"))

    if not exec_ok:
        return _classify_when_execution_success_false(base_out)

    if not final_ok:
        return _classify_when_final_not_proven(base_out)

    return (
        FAILURE_CODE_UNKNOWN_TERMINAL_FAILURE,
        FAILURE_STAGE_PROOF,
        "unclassified_post_trade_state",
    )


def _classify_when_execution_success_false(base_out: Dict[str, Any]) -> Tuple[str, str, str]:
    buy_ok = bool(base_out.get("buy_fill_confirmed"))
    sell_diag = base_out.get("sell_leg_diagnostics") or {}
    sell_place_ok = bool(sell_diag.get("place_success", True))
    sell_oid = str(sell_diag.get("order_id_sell") or "").strip()

    if not buy_ok:
        return (
            FAILURE_CODE_BUY_FILL_NOT_CONFIRMED,
            FAILURE_STAGE_BUY,
            _join_reasons(
                [
                    "buy_fill_confirmed_false",
                    str((base_out.get("buy_leg_diagnostics") or {}).get("actual_missing_or_false_input") or ""),
                ]
            ),
        )

    if not sell_place_ok:
        return (
            FAILURE_CODE_SELL_ORDER_SUBMIT_FAILED,
            FAILURE_STAGE_SELL,
            _join_reasons(
                [
                    "place_market_sell_failed",
                    str(sell_diag.get("actual_missing_or_false_input") or ""),
                ]
            ),
        )

    if not sell_oid:
        return FAILURE_CODE_SELL_ORDER_SUBMIT_FAILED, FAILURE_STAGE_SELL, "sell_order_id_missing_after_place"

    if not bool(base_out.get("sell_fill_confirmed")):
        return (
            FAILURE_CODE_SELL_FILL_NOT_CONFIRMED,
            FAILURE_STAGE_SELL,
            _join_reasons(
                [
                    "sell_fill_confirmed_false",
                    str(sell_diag.get("actual_missing_or_false_input") or ""),
                ]
            ),
        )

    pnl_diag = base_out.get("pnl_diagnostics") or {}
    if pnl_diag.get("net_pnl_is_none") and base_out.get("partial_failure_codes"):
        if "round_trip_incomplete" in (base_out.get("partial_failure_codes") or []):
            return FAILURE_CODE_PNL_VERIFICATION_FAILED, FAILURE_STAGE_PROOF, "round_trip_incomplete_pnl_not_verifiable"

    lw = base_out.get("local_write_diagnostics") or {}
    if not lw.get("databank_process_ok"):
        return FAILURE_CODE_DATABANK_WRITE_FAILED, FAILURE_STAGE_POST_TRADE_PIPELINE, "databank_process_ok_false"
    pipe = base_out.get("pipeline") or {}
    missing = []
    if not pipe.get("trade_memory_updated"):
        missing.append("trade_memory_updated_false")
    if not pipe.get("trade_events_appended"):
        missing.append("trade_events_appended_false")
    if not pipe.get("federated_includes_trade_id"):
        missing.append("federated_includes_trade_id_false")
    return (
        FAILURE_CODE_DATABANK_WRITE_FAILED,
        FAILURE_STAGE_POST_TRADE_PIPELINE,
        _join_reasons(missing) or "execution_success_false_pipeline_fields",
    )


def _classify_when_final_not_proven(base_out: Dict[str, Any]) -> Tuple[str, str, str]:
    """execution_success True but FINAL_EXECUTION_PROVEN false — supabase / gov / packet / scheduler."""
    gaps = _list_proof_contract_gaps(base_out)
    if "supabase_synced_false" in gaps:
        return (
            FAILURE_CODE_SUPABASE_SYNC_FAILED,
            FAILURE_STAGE_POST_TRADE_PIPELINE,
            _join_reasons(["supabase_synced_false", _supabase_diag_snippet(base_out)]),
        )
    if "governance_logged_false" in gaps:
        return (
            FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED,
            FAILURE_STAGE_POST_TRADE_PIPELINE,
            "governance_logged_false",
        )
    if "packet_updated_false" in gaps:
        return FAILURE_CODE_REVIEW_UPDATE_FAILED, FAILURE_STAGE_POST_TRADE_PIPELINE, "packet_updated_false"
    if "scheduler_stable_false" in gaps:
        return (
            FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED,
            FAILURE_STAGE_PROOF,
            _join_reasons(["scheduler_stable_false", _sched_snippet(base_out)]),
        )
    return (
        FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED,
        FAILURE_STAGE_PROOF,
        _join_reasons(gaps) or "FINAL_EXECUTION_PROVEN_false_unspecified",
    )


def _supabase_diag_snippet(base_out: Dict[str, Any]) -> str:
    d = base_out.get("supabase_sync_diagnostics") or {}
    if not isinstance(d, dict):
        return ""
    return str(d.get("last_error") or d.get("note") or "")[:500]


def _sched_snippet(base_out: Dict[str, Any]) -> str:
    st = base_out.get("stability") or {}
    if not isinstance(st, dict):
        return ""
    errs = st.get("errors") or []
    if errs:
        return f"stability_errors={errs[:3]!r}"
    return ""


def attach_terminal_failure_fields(base_out: Dict[str, Any]) -> None:
    """
    Mutate ``base_out`` with failure_stage, failure_code, failure_reason, final_execution_proven.

    On full success (execution_success and FINAL_EXECUTION_PROVEN), failure_* are set to None.
    ``error`` is mirrored to a non-null string whenever the cycle is not fully proven.
    """
    exec_ok = bool(base_out.get("execution_success"))
    final_ok = bool(base_out.get("FINAL_EXECUTION_PROVEN"))
    err_early = base_out.get("error")

    if exec_ok and final_ok:
        base_out["failure_stage"] = None
        base_out["failure_code"] = None
        base_out["failure_reason"] = None
        base_out["final_execution_proven"] = True
        base_out["error"] = None
        return

    base_out["final_execution_proven"] = final_ok

    # Early exit path: error set, no trade or partial
    if err_early:
        code, stage, reason = classify_early_guard_failure(str(err_early))
        base_out["failure_stage"] = stage
        base_out["failure_code"] = code
        base_out["failure_reason"] = reason
        base_out["error"] = reason
        return

    code, stage, reason = classify_post_trade_pipeline_failure(base_out)
    base_out["failure_stage"] = stage
    base_out["failure_code"] = code
    base_out["failure_reason"] = reason
    base_out["error"] = reason
    proof = base_out.get("proof")
    if isinstance(proof, dict):
        proof["failure_stage"] = stage
        proof["failure_code"] = code
        proof["failure_reason"] = reason


def proof_contract_violation_messages(g: Dict[str, Any]) -> List[str]:
    """Human-readable list of unmet proof contract fields (Gate A/B file → universal emit)."""
    if not g:
        return ["empty_proof"]
    msgs: List[str] = []
    if not g.get("FINAL_EXECUTION_PROVEN"):
        msgs.append("FINAL_EXECUTION_PROVEN_false")
    if g.get("partial_failure_codes"):
        msgs.append(f"partial_failure_codes:{g.get('partial_failure_codes')}")
    req = (
        "execution_success",
        "coinbase_order_verified",
        "databank_written",
        "supabase_synced",
        "governance_logged",
        "packet_updated",
        "scheduler_stable",
    )
    for k in req:
        if not g.get(k):
            msgs.append(f"{k}_false_or_missing")
    pnl_ok = g.get("pnl_calculation_verified")
    if pnl_ok is not True:
        msgs.append("pnl_calculation_verified_not_true")
    return msgs
