"""
Controlled live micro-validation streak — smallest-size real round trips (not first-20).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from trading_ai.deployment.deployment_checklist import run_deployment_checklist
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.deployment.ops_outputs_proof import run_ops_outputs_bundle
from trading_ai.deployment.paths import (
    deployment_data_dir,
    live_validation_runs_dir,
    ops_outputs_proof_path,
    reconciliation_proof_jsonl_path,
    streak_state_path,
    supabase_proof_jsonl_path,
)
from trading_ai.deployment.reconciliation_proof import prove_reconciliation_after_trade
from trading_ai.deployment.supabase_proof import prove_supabase_write
from trading_ai.control.spot_operator_status import write_spot_operator_snapshots
from trading_ai.nte.execution.product_rules import venue_min_notional_usd
from trading_ai.nte.spot_inventory_snapshot import snapshot_live_spot_ledger
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.runtime_proof.coinbase_accounts import resolve_validation_market_product
from trading_ai.runtime_proof.live_execution_validation import run_single_live_execution_validation

logger = logging.getLogger(__name__)


def materialize_adaptive_proofs_for_micro_validation(product_id: str) -> Dict[str, Any]:
    """
    Real adaptive OS + routing evaluation (not stubs) so ``adaptive_live_proof.json`` and
    ``adaptive_routing_proof.json`` exist under EZRAS_RUNTIME_ROOT before live round-trips.

    Uses the same ``coinbase_entry_adaptive_gate`` + ``compute_live_gate_allocation`` stack as Avenue A,
    with regime label ``micro_validation`` and neutral equity hints.
    """
    from trading_ai.control.adaptive_routing_live import compute_live_gate_allocation
    from trading_ai.control.live_adaptive_integration import coinbase_entry_adaptive_gate

    pid = str(product_id or "BTC-USD").strip() or "BTC-USD"
    ag = coinbase_entry_adaptive_gate(
        equity=100_000.0,
        rolling_equity_high=100_000.0,
        market_regime="micro_validation",
        market_chop_score=0.35,
        slippage_health=0.85,
        liquidity_health=0.85,
        product_id=pid,
        proof_context={
            "route": "micro_validation_streak_preamble",
            "trade_intent": "preamble_materialize_adaptive_proofs",
            "proof_source": "trading_ai.deployment.live_micro_validation:materialize_adaptive_proofs_for_micro_validation",
        },
    )
    rep = {}
    praw = ag.get("proof")
    if isinstance(praw, dict):
        rep = praw.get("report") or {}
    rout = compute_live_gate_allocation(
        aos_report=rep if isinstance(rep, dict) else {},
        market_quality_allows_adaptive=bool((rep or {}).get("confidence_scaling_ready") or True),
        entrypoint="deployment.live_micro_validation_streak",
        route="micro_validation_streak_preamble",
        venue="coinbase",
        product_id=pid,
    )
    return {"adaptive_gate": ag, "routing": rout, "product_id": pid}


def _proof_references_for_run(run_index: int, run_json_path: Path) -> Dict[str, Union[int, str]]:
    root = ezras_runtime_root()
    ctrl = root / "data" / "control"
    return {
        "run_index": run_index,
        "live_validation_run_json": str(run_json_path),
        "live_validation_streak_json": str(streak_state_path()),
        "reconciliation_proof_jsonl": str(reconciliation_proof_jsonl_path()),
        "supabase_proof_jsonl": str(supabase_proof_jsonl_path()),
        "ops_outputs_proof_json": str(ops_outputs_proof_path()),
        "execution_proof_json": str(root / "execution_proof" / "live_execution_validation.json"),
        "deployment_data_dir": str(deployment_data_dir()),
        "runtime_policy_snapshot_json": str(ctrl / "runtime_policy_snapshot.json"),
        "validation_product_resolution_report_json": str(ctrl / "validation_product_resolution_report.json"),
        "quote_capital_truth_json": str(ctrl / "quote_capital_truth.json"),
    }


def _requested_quote_usd() -> tuple[float, str]:
    """
    Configured request (not yet clamped to venue minimum).

    Precedence: LIVE_MICRO_VALIDATION_QUOTE_USD, then DEPLOYMENT_VALIDATION_QUOTE_USD, else 5.0 USD.
    """
    raw = (os.environ.get("LIVE_MICRO_VALIDATION_QUOTE_USD") or "").strip()
    if raw:
        try:
            return max(1.0, float(raw)), "LIVE_MICRO_VALIDATION_QUOTE_USD"
        except ValueError:
            pass
    raw2 = (os.environ.get("DEPLOYMENT_VALIDATION_QUOTE_USD") or "").strip()
    if raw2:
        try:
            return max(1.0, float(raw2)), "DEPLOYMENT_VALIDATION_QUOTE_USD"
        except ValueError:
            pass
    return 5.0, "default_5_usd"


def _preresolve_chosen_quote_usd(
    requested_global: float,
    product_id: str,
    runtime_root: Path,
) -> Tuple[float, str]:
    """
    Smallest live quote for this venue path: max(requested, venue min) with balance preflight.

    Returns ``(chosen_quote_usd, resolved_product_id)`` (e.g. BTC-USD vs BTC-USDC).

    Raises RuntimeError on preflight failure (exact message is the blocking reason).
    """
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    client = CoinbaseClient()
    trial = max(requested_global, venue_min_notional_usd(product_id))
    cp, _diag, qerr = resolve_validation_market_product(
        client,
        quote_notional=float(trial),
        preferred_product_id=product_id,
        write_control_artifacts=True,
        runtime_root=runtime_root,
    )
    if qerr:
        raise RuntimeError(f"quote_precheck_failed:{qerr}")
    vmin_res = float(venue_min_notional_usd(cp))
    chosen_quote = max(requested_global, vmin_res, trial)
    _cp2, _d2, qerr2 = resolve_validation_market_product(
        client,
        quote_notional=float(chosen_quote),
        preferred_product_id=product_id,
        write_control_artifacts=True,
        runtime_root=runtime_root,
    )
    if qerr2:
        raise RuntimeError(f"quote_precheck_failed:{qerr2}")
    return float(chosen_quote), str(_cp2).strip()


def _run_record_proof_keys() -> Tuple[str, ...]:
    return (
        "buy_fill_confirmed",
        "sell_fill_confirmed",
        "base_quote_truth_ok",
        "oversell_ok",
        "reconciliation_ok",
        "local_write_ok",
        "supabase_ok",
        "governance_ok",
        "review_packet_ok",
        "pnl_verified",
        "no_partial_failures",
    )


def _failed_proof_fields(rec: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if rec.get("proof_na_due_to_pre_execution_block"):
        return []
    for k in _run_record_proof_keys():
        if k == "no_partial_failures":
            if rec.get(k) is False:
                out.append(k)
        elif k == "pnl_verified" and str(rec.get("pnl_verified_scope") or "").startswith(
            "not_applicable"
        ):
            continue
        elif not rec.get(k):
            out.append(k)
    return out


def _execution_root_analysis(
    raw: Dict[str, Any],
    *,
    checklist: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Classify the true causal failure so streak blocking_reason is not a downstream false cascade
    (e.g. supabase_proof_failed when no buy was ever placed).
    """
    err = raw.get("error")
    es = str(err or "")
    has_buy = bool(str(raw.get("order_id_buy") or "").strip())
    out: Dict[str, Any] = {
        "pre_execution_blocked": False,
        "root_failure_stage": "execution_attempted",
        "root_failure_reason_code": None,
        "downstream_failures_expected_from_root_block": False,
        "streak_blocking_reason": None,
        "operator_plain_english": "",
    }
    if not err:
        return out

    if es.startswith("quote_precheck_failed:runtime_policy_empty_or_invalid"):
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "runtime_allowlist_configuration"
        out["root_failure_reason_code"] = "runtime_policy_empty_or_invalid"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = "runtime_policy_empty_or_invalid"
        out["operator_plain_english"] = (
            "NTE_PRODUCTS / runtime allowlist is empty or unusable after env override. "
            "Restore at least one spot product id or clear env to use code defaults."
        )
        return out

    if es.startswith("quote_precheck_failed:runtime_policy_disallows_fundable_product"):
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "validation_product_preflight"
        out["root_failure_reason_code"] = "validation_product_policy_failure"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = (
            "validation_product_policy_failure:runtime_policy_disallows_fundable_product"
        )
        out["operator_plain_english"] = (
            "Quote exists for a fundable route (e.g. USDC for BTC-USDC) but runtime policy does not "
            "allow that product_id. Either add it to NTE_PRODUCTS / runtime allowlist or fund the "
            "quote currency required by an allowed product (e.g. USD for BTC-USD). "
            "See quote_diagnostics.candidate_attempts and funding_vs_policy_lens."
        )
        return out

    if es.startswith("quote_precheck_failed:insufficient_allowed_quote_balance"):
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "validation_product_preflight"
        out["root_failure_reason_code"] = "validation_quote_insufficient"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = (
            "validation_quote_preflight:insufficient_allowed_quote_balance"
        )
        out["operator_plain_english"] = (
            "No runtime-allowed validation candidate has enough balance in its required quote currency "
            "for the configured notional. Fund USD/USDC (or other quote) per candidate_evaluations."
        )
        return out

    if es.startswith("quote_precheck_failed:no_runtime_supported_validation_product"):
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "validation_product_preflight"
        out["root_failure_reason_code"] = "validation_venue_catalog_unsupported"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = (
            "validation_quote_preflight:no_runtime_supported_validation_product"
        )
        out["operator_plain_english"] = (
            "Allowed products are missing from the Coinbase venue catalog or marked offline. "
            "Check runtime_policy_snapshot and venue connectivity."
        )
        return out

    if es.startswith("quote_precheck_failed:no_allowed_validation_product_found"):
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "validation_product_preflight"
        out["root_failure_reason_code"] = "no_allowed_validation_product_found"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = (
            "validation_quote_preflight:no_allowed_validation_product_found"
        )
        out["operator_plain_english"] = (
            "Validation could not select a single-leg spot product after venue, policy, balance, "
            "and ticker checks — see validation_product_resolution_report.json candidate_attempts."
        )
        return out

    if es.startswith("quote_precheck_failed:no_allowed_validation_product"):
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "validation_product_preflight"
        out["root_failure_reason_code"] = "validation_product_policy_failure"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = "validation_product_policy_failure:no_allowed_validation_product"
        out["operator_plain_english"] = (
            "Micro-validation could not pick any product that is both funded and allowed by NTE "
            "settings (products allowlist). Add the needed product (e.g. BTC-USDC) to NTE products "
            "or fund USD for BTC-USD. See quote_diagnostics.candidate_attempts in execution payload."
        )
        return out

    if es.startswith("quote_precheck_failed:"):
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "validation_quote_preflight"
        out["root_failure_reason_code"] = "validation_product_policy_failure"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = "validation_product_policy_failure:" + es.split(":", 1)[-1][:80]
        out["operator_plain_english"] = "Quote preflight failed before any order — see execution error."
        return out

    if es.startswith("buy_failed:") and "product_not_allowed" in es:
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "live_order_guard"
        out["root_failure_reason_code"] = "validation_product_live_guard_rejected"
        out["downstream_failures_expected_from_root_block"] = True
        vid = str(raw.get("venue_product_id") or "")
        out["streak_blocking_reason"] = f"validation_product_policy_failure:product_not_allowed:{vid}"
        out["operator_plain_english"] = (
            f"Live order guard rejected venue product {vid!r} (product_not_allowed). "
            "Selector and NTE products allowlist were misaligned — fixed in resolve_validation_market_product."
        )
        return out

    if es.startswith("buy_failed:") and not has_buy:
        out["pre_execution_blocked"] = True
        out["root_failure_stage"] = "buy_order_rejected"
        out["root_failure_reason_code"] = "buy_failed_pre_fill"
        out["downstream_failures_expected_from_root_block"] = True
        out["streak_blocking_reason"] = "buy_failed_before_fill:" + es[:120]
        out["operator_plain_english"] = "Buy did not succeed — no fill to verify downstream."
        return out

    if "governance_blocked" in es:
        out["root_failure_reason_code"] = "governance_blocked"
        out["streak_blocking_reason"] = es[:200]
        return out

    return out


def _classify_run_outcome(raw: Dict[str, Any], rec: Dict[str, Any]) -> str:
    err = raw.get("error")
    if err:
        es = str(err)
        if "governance_blocked" in es or "missing_or_invalid" in es or "not_enabled" in es:
            return "blocked_before_execution"
        if "runtime_policy_disallows_fundable_product" in es:
            return "validation_product_policy_failure"
        if "insufficient_allowed_quote_balance" in es:
            return "validation_quote_insufficient"
        if "no_runtime_supported_validation_product" in es:
            return "validation_venue_unsupported"
        if "no_allowed_validation_product_found" in es:
            return "quote_precheck_failed"
        if "no_allowed_validation_product" in es:
            return "validation_product_policy_failure"
        if "product_not_allowed" in es:
            return "validation_product_policy_failure"
        if "buy_failed" in es or es.startswith("quote_precheck"):
            return "buy_failed"
        return "blocked_or_failed_before_round_trip"
    if not rec.get("buy_fill_confirmed"):
        return "buy_failed"
    if not rec.get("sell_fill_confirmed"):
        return "sell_failed"
    ff = _failed_proof_fields(rec)
    if ff:
        return "proof_failed_after_execution"
    return "passed"


def _trade_path_stage_reached(raw: Dict[str, Any]) -> str:
    err = raw.get("error")
    if err:
        return "blocked:" + str(err)[:120]
    if not raw.get("order_id_buy"):
        return "post_governance_pre_buy"
    if raw.get("buy_fill_confirmed") is False:
        return "post_buy_fill_unconfirmed"
    if not raw.get("order_id_sell"):
        return "post_buy_pre_sell_order"
    if raw.get("sell_fill_confirmed") is False:
        return "post_sell_fill_unconfirmed"
    if not raw.get("coinbase_order_verified"):
        return "buy_or_sell_not_verified"
    if not raw.get("execution_success"):
        return "post_execution_pipeline_incomplete"
    return "round_trip_pipeline_evaluated"


def _build_run_record(
    idx: int,
    raw: Dict[str, Any],
    *,
    recon_ok: bool,
    supa_ok: bool,
    requested_notional_usd: float,
    venue_min_notional_usd: float,
    chosen_notional_usd: float,
    checklist: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    checklist = checklist or {}
    root_info = _execution_root_analysis(raw, checklist=checklist)
    pre_na = bool(root_info.get("pre_execution_blocked"))

    pnl_verified = bool(raw.get("pnl_calculation_verified"))
    pnl_scope = "verified" if pnl_verified else "unverified"
    no_partial = len(raw.get("partial_failure_codes") or []) == 0
    tid = raw.get("trade_id")
    oid_buy = str(raw.get("order_id_buy") or "").strip()
    oid_sell = str(raw.get("order_id_sell") or "").strip()
    coin_ok = bool(raw.get("coinbase_order_verified"))
    pipe = raw.get("pipeline") if isinstance(raw.get("pipeline"), dict) else {}

    if pre_na:
        pnl_scope = "not_applicable_due_to_no_round_trip"
        pnl_verified = False

    if raw.get("buy_fill_confirmed") is not None:
        buy_fc = bool(raw.get("buy_fill_confirmed"))
    else:
        buy_fc = coin_ok and bool(oid_buy)
    if raw.get("sell_fill_confirmed") is not None:
        sell_fc = bool(raw.get("sell_fill_confirmed"))
    else:
        sell_fc = coin_ok and bool(oid_sell)
    if raw.get("base_quote_truth_ok") is not None:
        bq_ok = bool(raw.get("base_quote_truth_ok"))
    else:
        bq_ok = coin_ok

    if pre_na:
        buy_fc = False
        sell_fc = False
        bq_ok = False

    if raw.get("local_write_evidence_ok") is not None:
        local_ok = bool(raw.get("local_write_evidence_ok"))
    else:
        local_ok = bool(
            raw.get("databank_written")
            and pipe.get("trade_events_appended")
            and pipe.get("trade_memory_updated")
        )

    if pre_na:
        local_ok = True
        local_na = True
    else:
        local_na = False

    supa_disp = bool(raw.get("supabase_synced")) and supa_ok
    gov_disp = bool(raw.get("governance_logged"))
    pkt_disp = bool(raw.get("packet_updated"))
    if pre_na:
        supa_disp = True
        supa_na = True
        gov_disp = bool(checklist.get("governance_trading_permitted", False))
        pkt_disp = True
        pkt_na = True
    else:
        supa_na = False
        pkt_na = False

    out = {
        "run_index": idx,
        "trade_id": tid,
        "requested_notional_usd": requested_notional_usd,
        "venue_min_notional_usd": venue_min_notional_usd,
        "chosen_notional_usd": chosen_notional_usd,
        "venue_product_id": raw.get("venue_product_id"),
        "buy_fill_confirmed": buy_fc,
        "sell_fill_confirmed": sell_fc,
        "base_quote_truth_ok": bq_ok,
        "oversell_ok": not bool(raw.get("oversell_risk")),
        "reconciliation_ok": recon_ok,
        "local_write_ok": local_ok,
        "supabase_ok": supa_disp,
        "governance_ok": gov_disp,
        "review_packet_ok": pkt_disp,
        "pnl_verified": pnl_verified,
        "pnl_verified_scope": pnl_scope,
        "no_partial_failures": no_partial,
        "raw_summary": {k: raw.get(k) for k in (
            "error",
            "execution_success",
            "supabase_synced",
            "governance_logged",
            "packet_updated",
            "READY_FOR_FIRST_20",
            "partial_failure_codes",
            "order_id_buy",
            "order_id_sell",
            "buy_fill_truth_source",
            "sell_fill_truth_source",
            "coinbase_order_verified",
            "buy_fill_confirmed",
            "sell_fill_confirmed",
            "base_quote_truth_ok",
            "local_write_evidence_ok",
        )},
    }
    out["partial_failure_sources"] = list(raw.get("partial_failure_codes") or [])
    out["classified_run_outcome"] = _classify_run_outcome(raw, out)
    out["trade_path_stage_reached"] = _trade_path_stage_reached(raw)
    out["pipeline_diagnostics"] = {
        "trade_memory_updated": pipe.get("trade_memory_updated"),
        "trade_events_appended": pipe.get("trade_events_appended"),
        "federated_includes_trade_id": pipe.get("federated_includes_trade_id"),
        "supabase_upsert_true": pipe.get("supabase_upsert_true"),
        "supabase_row_exists": pipe.get("supabase_row_exists"),
        "governance_log_has_entry": pipe.get("governance_log_has_entry"),
        "review_packet_updated": pipe.get("review_packet_updated"),
        "pipeline_notes": pipe.get("pipeline_notes") or [],
    }
    out["sell_leg_diagnostics"] = raw.get("sell_leg_diagnostics") if isinstance(
        raw.get("sell_leg_diagnostics"), dict
    ) else {}
    out["pnl_diagnostics"] = raw.get("pnl_diagnostics") if isinstance(
        raw.get("pnl_diagnostics"), dict
    ) else {}
    out["local_write_diagnostics"] = raw.get("local_write_diagnostics") if isinstance(
        raw.get("local_write_diagnostics"), dict
    ) else {}
    out["proof_na_due_to_pre_execution_block"] = pre_na
    out["supabase_proof_not_applicable"] = supa_na
    out["review_packet_not_applicable"] = pkt_na
    out["local_write_not_applicable"] = local_na
    out["pre_execution_blocked"] = root_info.get("pre_execution_blocked")
    out["root_failure_stage"] = root_info.get("root_failure_stage")
    out["root_failure_reason_code"] = root_info.get("root_failure_reason_code")
    out["downstream_failures_expected_from_root_block"] = root_info.get(
        "downstream_failures_expected_from_root_block"
    )
    out["operator_plain_english_root_cause"] = root_info.get("operator_plain_english") or ""

    out["failed_proof_fields"] = _failed_proof_fields(out)

    proof_row_ok = all(
        (
            out["buy_fill_confirmed"],
            out["sell_fill_confirmed"],
            out["base_quote_truth_ok"],
            out["oversell_ok"],
            out["reconciliation_ok"],
            out["local_write_ok"],
            out["supabase_ok"],
            out["governance_ok"],
            out["review_packet_ok"],
            out["pnl_verified"],
            out["no_partial_failures"],
        )
    )
    out["micro_validation_row_pass"] = proof_row_ok

    out["run_gate_failure_reason"] = None
    if pre_na and root_info.get("root_failure_reason_code"):
        out["run_gate_failure_reason"] = (
            "pre_execution_block:" + str(root_info.get("root_failure_reason_code"))
            + ":"
            + str(raw.get("error") or "")[:160]
        )
    elif not proof_row_ok:
        out["run_gate_failure_reason"] = (
            "run_record_gate_failed:"
            + ",".join(_failed_proof_fields(out))
            + (
                ";partial:" + ",".join(out["partial_failure_sources"])
                if out["partial_failure_sources"]
                else ""
            )
        )
    return out


def run_live_micro_validation_streak(
    n: int = 3,
    *,
    runtime_root: Optional[Path] = None,
    product_id: str = "BTC-USD",
) -> Dict[str, Any]:
    """
    Run ``n`` smallest-notional round trips. Stops on first failure.

    Requires :func:`run_deployment_checklist` ``ready_for_live_micro_validation``.
    Does **not** start first-20 or scale size.
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    deployment_data_dir().mkdir(parents=True, exist_ok=True)
    live_validation_runs_dir()

    preamble: Dict[str, Any] = {}
    checklist = run_deployment_checklist(write_files=False)
    requested_global, quote_env_source = _requested_quote_usd()
    if not checklist.get("ready_for_live_micro_validation"):
        msg = "deployment_checklist_not_ready_for_live_micro_validation"
        _write_streak(
            False,
            msg,
            [],
            n,
            0,
            0,
            requested_global,
            None,
            None,
            [],
            streak_status="never_started_checklist_blocked",
            checklist_blockers=list(checklist.get("blocking_reasons") or []),
        )
        return {
            "ok": False,
            "live_validation_streak_passed": False,
            "blocking_reason": msg,
            "streak_status": "never_started_checklist_blocked",
            "streak_never_started": True,
            "checklist_blocking_reasons": list(checklist.get("blocking_reasons") or []),
            "checklist": checklist,
            "requested_notional_usd": requested_global,
            "quote_config_source": quote_env_source,
            "chosen_notional_usd": None,
            "venue_min_notional_usd": None,
            "n_requested": n,
            "n_completed": 0,
            "passed_run_count": 0,
            "failed_run_count": 0,
        }

    try:
        preamble = materialize_adaptive_proofs_for_micro_validation(product_id)
    except Exception as exc:
        logger.warning("micro_validation adaptive proof materialize: %s", exc)
        preamble = {"error": str(exc)}

    runs: List[Dict[str, Any]] = []
    passed = True
    blocking: Optional[str] = None

    mv_session = uuid.uuid4().hex
    _mv_env_keys = (
        "EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE",
        "EZRAS_MICRO_VALIDATION_SESSION_ID",
        "EZRAS_MICRO_VALIDATION_RUN_INDEX",
    )
    _mv_env_saved = {k: os.environ.get(k) for k in _mv_env_keys}
    try:
        os.environ["EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE"] = "1"
        os.environ["EZRAS_MICRO_VALIDATION_SESSION_ID"] = mv_session
        for i in range(1, n + 1):
            os.environ["EZRAS_MICRO_VALIDATION_RUN_INDEX"] = str(i)
            try:
                chosen_quote, resolved_product_id = _preresolve_chosen_quote_usd(
                    requested_global,
                    product_id,
                    root,
                )
            except RuntimeError as exc:
                blocking = str(exc)
                _write_streak(
                    False,
                    blocking,
                    runs,
                    n,
                    len(runs),
                    1,
                    requested_global,
                    None,
                    None,
                    [r.get("proof_references") for r in runs],
                    streak_status="never_started_quote_preflight_failed",
                    checklist_blockers=None,
                )
                return {
                    "ok": False,
                    "live_validation_streak_passed": False,
                    "streak_status": "never_started_quote_preflight_failed",
                    "streak_never_started": True,
                    "blocking_reason": blocking,
                    "runs": runs,
                    "proof_references_by_run": [r.get("proof_references") for r in runs],
                    "requested_notional_usd": requested_global,
                    "quote_config_source": quote_env_source,
                    "chosen_notional_usd": None,
                    "venue_min_notional_usd": None,
                    "n_requested": n,
                    "n_completed": len(runs),
                    "passed_run_count": len(runs),
                    "failed_run_count": 1,
                }
            except Exception as exc:
                logger.exception("micro-validation pre-resolve")
                blocking = f"quote_precheck_exception:{type(exc).__name__}:{exc}"
                _write_streak(
                    False,
                    blocking,
                    runs,
                    n,
                    len(runs),
                    1,
                    requested_global,
                    None,
                    None,
                    [r.get("proof_references") for r in runs],
                    streak_status="never_started_quote_preflight_failed",
                    checklist_blockers=None,
                )
                return {
                    "ok": False,
                    "live_validation_streak_passed": False,
                    "streak_status": "never_started_quote_preflight_failed",
                    "streak_never_started": True,
                    "blocking_reason": blocking,
                    "runs": runs,
                    "proof_references_by_run": [r.get("proof_references") for r in runs],
                    "requested_notional_usd": requested_global,
                    "quote_config_source": quote_env_source,
                    "chosen_notional_usd": None,
                    "venue_min_notional_usd": None,
                    "n_requested": n,
                    "n_completed": len(runs),
                    "passed_run_count": len(runs),
                    "failed_run_count": 1,
                }

            snap_before = snapshot_live_spot_ledger(str(resolved_product_id).strip())

            raw = run_single_live_execution_validation(
                root,
                quote_usd=float(chosen_quote),
                product_id=product_id,
                include_runtime_stability=False,
            )
            apid = str(raw.get("venue_product_id") or product_id).strip()
            requested_usd = requested_global
            venue_min_usd = float(venue_min_notional_usd(apid))
            chosen_usd = float(chosen_quote)

            tid = str(raw.get("trade_id") or "")

            snap_after = snapshot_live_spot_ledger(apid)
            rp = raw.get("realized_pnl") if isinstance(raw.get("realized_pnl"), dict) else {}
            fs = raw.get("flatten_sizing") if isinstance(raw.get("flatten_sizing"), dict) else {}
            ctx: Dict[str, Any] = {
                "product_id": apid,
                "trade_id": tid,
                "buy_quote_spent": fs.get("buy_quote_spent") or rp.get("buy_quote_spent"),
                "sell_quote_received": rp.get("sell_quote_received"),
                "exchange_open_orders_count": 0,
                "oversell_risk": bool(raw.get("oversell_risk")),
                "reconciliation_mode": "inventory_delta",
                "baseline_exchange_base_qty": snap_before.get("exchange_base_qty"),
                "baseline_internal_base_qty": snap_before.get("internal_base_qty"),
                "imported_inventory_baseline": bool(snap_before.get("imported_inventory_baseline")),
                "validation_base_asset": snap_after.get("validation_base_asset"),
                "validation_quote_asset": snap_after.get("validation_quote_asset"),
                "quote_available_before": snap_before.get("quote_available_combined_usd"),
                "total_spot_equity_before": snap_before.get("total_spot_equity_usd"),
                "exchange_base_qty_before": snap_before.get("exchange_base_qty"),
                "internal_base_qty_before": snap_before.get("internal_base_qty"),
            }
            recon = prove_reconciliation_after_trade(ctx, append_log=True)
            recon_ok = bool(recon.get("reconciliation_ok"))

            root_snap = _execution_root_analysis(raw, checklist=checklist)
            expect_supa_row = bool(str(raw.get("order_id_buy") or "").strip())
            if tid and expect_supa_row:
                supa = prove_supabase_write(tid, append_log=True)
            else:
                supa = {
                    "supabase_proof_ok": True,
                    "proof_skipped_reason": "no_buy_order_executed_trade_row_not_expected",
                }
            supa_ok = bool(supa.get("supabase_proof_ok"))

            if root_snap.get("pre_execution_blocked") and root_snap.get("streak_blocking_reason"):
                passed = False
                blocking = str(root_snap["streak_blocking_reason"])
            elif not recon_ok:
                passed = False
                blocking = "reconciliation_proof_failed:" + ",".join(recon.get("notes") or [])
            elif not supa_ok:
                passed = False
                blocking = "supabase_proof_failed"

            rec = _build_run_record(
                i,
                raw,
                recon_ok=recon_ok,
                supa_ok=supa_ok,
                requested_notional_usd=requested_usd,
                venue_min_notional_usd=venue_min_usd,
                chosen_notional_usd=chosen_usd,
                checklist=checklist,
            )
            fn = live_validation_runs_dir() / f"live_validation_{i:03d}.json"
            rec["proof_references"] = _proof_references_for_run(i, fn)
            if not rec.get("micro_validation_row_pass", False):
                passed = False
                blocking = blocking or (rec.get("run_gate_failure_reason") or "run_record_gate_failed")

            mv_meta = {
                "session_id": mv_session,
                "run_index": i,
                "duplicate_guard_mode": "deployment_micro_validation_isolated_keys",
                "honesty": (
                    "Each streak iteration uses a distinct failsafe duplicate key (session + run). "
                    "This is not a global duplicate bypass."
                ),
            }
            fn.write_text(
                json.dumps(
                    {
                        "generated_at": iso_now(),
                        "quote_config_source": quote_env_source,
                        "requested_notional_usd": requested_usd,
                        "venue_min_notional_usd": venue_min_usd,
                        "chosen_notional_usd": chosen_usd,
                        "deployment_micro_validation": mv_meta,
                        "run": rec,
                        "execution": raw,
                        "reconciliation": recon,
                        "supabase_proof": supa,
                        "spot_snapshot_before": snap_before,
                        "spot_snapshot_after": snap_after,
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            runs.append(rec)

            try:
                write_spot_operator_snapshots(snap_after, reconciliation=recon, append=True)
            except Exception as exc:
                logger.warning("spot_operator_snapshots: %s", exc)

            try:
                run_ops_outputs_bundle(runtime_root=root)
            except Exception as exc:
                logger.error("ops_outputs_bundle failed (streak hard-fail): %s", exc)
                passed = False
                blocking = blocking or f"ops_outputs:{type(exc).__name__}:{exc}"

            if not passed:
                break

    finally:
        for _k, _v in _mv_env_saved.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v

    if passed:
        pr, fr = len(runs), 0
    else:
        pr = max(0, len(runs) - 1)
        fr = 1 if runs else 0

    last_chosen = runs[-1]["chosen_notional_usd"] if runs else None
    last_vmin = runs[-1]["venue_min_notional_usd"] if runs else None

    streak_status = "passed" if passed else ("started_and_failed" if runs else "never_started")
    _write_streak(
        passed,
        blocking or "",
        runs,
        n,
        pr,
        fr,
        requested_global,
        last_chosen,
        last_vmin,
        [r.get("proof_references") for r in runs],
        streak_status=streak_status,
        checklist_blockers=None,
        deployment_micro_validation_duplicate_policy={
            "session_id": mv_session,
            "mode": "isolated_failsafe_duplicate_keys_per_streak_run",
            "environment": {
                "EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE": "set_during_streak_loop",
                "EZRAS_MICRO_VALIDATION_SESSION_ID": "uuid_per_streak",
                "EZRAS_MICRO_VALIDATION_RUN_INDEX": "1..n_per_iteration",
            },
            "honesty": (
                "Each iteration uses a distinct failsafe duplicate key (product:action:valscope:session_run). "
                "Ordinary live entries unchanged."
            ),
        },
    )

    return {
        "ok": passed,
        "live_validation_streak_passed": passed,
        "streak_status": streak_status,
        "streak_never_started": not runs and not passed,
        "blocking_reason": blocking,
        "runs": runs,
        "proof_references_by_run": [r.get("proof_references") for r in runs],
        "requested_notional_usd": requested_global,
        "quote_config_source": quote_env_source,
        "chosen_notional_usd": last_chosen,
        "quote_usd": last_chosen,
        "venue_min_notional_usd": last_vmin,
        "n_requested": n,
        "n_completed": len(runs),
        "passed_run_count": pr,
        "failed_run_count": fr,
        "adaptive_proof_preamble": preamble,
        "deployment_micro_validation_session_id": mv_session,
    }


def diagnose_micro_validation_trade(
    trade_id: str,
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Operator inspection: load the saved ``live_validation_*.json`` for ``trade_id`` (if present)
    and return a compact diagnosis (order ids, fill sources, PnL, local write, proof failures).
    """
    tid = str(trade_id or "").strip()
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    runs_dir = live_validation_runs_dir()
    hit_path: Optional[Path] = None
    payload: Optional[Dict[str, Any]] = None
    if runs_dir.is_dir():
        for p in sorted(runs_dir.glob("live_validation_*.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            ex = data.get("execution") if isinstance(data.get("execution"), dict) else {}
            run_rec = data.get("run") if isinstance(data.get("run"), dict) else {}
            if str(run_rec.get("trade_id") or ex.get("trade_id") or "") == tid:
                hit_path = p
                payload = data
                break
    if not payload or not isinstance(payload, dict):
        return {
            "trade_id": tid,
            "found": False,
            "runtime_root": str(root),
            "searched_dir": str(runs_dir),
            "message": "no_live_validation_json_for_trade_id",
        }
    raw = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    rec = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    rp = raw.get("realized_pnl") if isinstance(raw.get("realized_pnl"), dict) else {}
    return {
        "trade_id": tid,
        "found": True,
        "run_json_path": str(hit_path) if hit_path else None,
        "runtime_root": str(root),
        "order_id_buy": raw.get("order_id_buy"),
        "order_id_sell": raw.get("order_id_sell"),
        "buy_fill_truth_source": raw.get("buy_fill_truth_source"),
        "sell_fill_truth_source": raw.get("sell_fill_truth_source"),
        "buy_quote_spent": (raw.get("flatten_sizing") or {}).get("buy_quote_spent")
        if isinstance(raw.get("flatten_sizing"), dict)
        else None,
        "sell_quote_received": rp.get("sell_quote_received"),
        "base_quote_truth_ok": raw.get("base_quote_truth_ok"),
        "pnl_verified": raw.get("pnl_calculation_verified"),
        "realized_pnl_complete": rp.get("complete"),
        "local_write_evidence_ok": raw.get("local_write_evidence_ok"),
        "databank_written": raw.get("databank_written"),
        "supabase_synced": raw.get("supabase_synced"),
        "execution_success": raw.get("execution_success"),
        "coinbase_order_verified": raw.get("coinbase_order_verified"),
        "failed_proof_fields": rec.get("failed_proof_fields"),
        "classified_run_outcome": rec.get("classified_run_outcome"),
        "partial_failure_codes": raw.get("partial_failure_codes"),
        "sell_leg_diagnostics": raw.get("sell_leg_diagnostics"),
        "pnl_diagnostics": raw.get("pnl_diagnostics"),
        "local_write_diagnostics": raw.get("local_write_diagnostics"),
    }


def _write_streak(
    passed: bool,
    blocking: str,
    runs: List[Dict[str, Any]],
    n: int,
    passed_run_count: int,
    failed_run_count: int,
    requested_notional_usd: Optional[float],
    chosen_notional_usd: Optional[float],
    venue_min_notional_usd: Optional[float],
    proof_refs_by_run: List[Any],
    *,
    streak_status: str = "unknown",
    checklist_blockers: Optional[List[str]] = None,
    deployment_micro_validation_duplicate_policy: Optional[Dict[str, Any]] = None,
) -> None:
    payload = {
        "generated_at": iso_now(),
        "live_validation_streak_passed": passed,
        "streak_status": streak_status,
        "streak_interpretation": (
            "never_started_checklist_blocked: checklist was not green — no live orders were sent."
            if streak_status == "never_started_checklist_blocked"
            else (
                "never_started_quote_preflight_failed: checklist was green but quote/balance preflight failed — no orders sent."
                if streak_status == "never_started_quote_preflight_failed"
                else (
                    "started_and_failed: at least one round-trip was attempted; see runs and blocking_reason."
                    if streak_status == "started_and_failed"
                    else (
                        "passed: all requested runs completed successfully."
                        if streak_status == "passed"
                        else "see blocking_reason and n_completed."
                    )
                )
            )
        ),
        "checklist_blockers_when_never_started": checklist_blockers,
        "blocking_reason": blocking,
        "requested_notional_usd": requested_notional_usd,
        "chosen_notional_usd": chosen_notional_usd,
        "quote_usd": chosen_notional_usd,
        "venue_min_notional_usd": venue_min_notional_usd,
        "runs": runs,
        "proof_references_by_run": proof_refs_by_run,
        "n_requested": n,
        "n_completed": len(runs),
        "passed_run_count": passed_run_count,
        "failed_run_count": failed_run_count,
        "deployment_micro_validation_duplicate_policy": deployment_micro_validation_duplicate_policy,
    }
    streak_state_path().write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
