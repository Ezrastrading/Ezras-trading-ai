"""Coherent validation product resolution — single-leg spot only; aligned with runtime policy."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from trading_ai.nte.execution.routing.core.universal_types import ValidationResolution
from trading_ai.nte.execution.routing.integration.capital_reports import (
    build_deployable_capital_report,
    build_portfolio_truth_snapshot_dict,
    build_route_selection_report,
    normalized_wallet_rows_from_balances,
)
from trading_ai.nte.execution.routing.integration.control_artifacts import write_validation_control_artifacts
from trading_ai.nte.execution.product_rules import venue_min_notional_usd
from trading_ai.nte.execution.routing.integration.spot_quote_utils import (
    is_spot_like_product_id,
    parse_spot_base_quote,
)
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import (
    CoinbaseRuntimeProductPolicy,
    product_supported_by_runtime_venue_catalog,
    resolve_coinbase_runtime_product_policy,
)
from trading_ai.nte.execution.routing.policy.universal_runtime_policy import (
    build_universal_runtime_policy,
    policy_vs_capital_one_liner,
)
from trading_ai.nte.hardening import coinbase_product_policy as _nte_prod_policy
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.runtime_proof import coinbase_accounts as _cb_acct


def merge_validation_candidates_for_runtime(pol: CoinbaseRuntimeProductPolicy) -> List[str]:
    """
    Full priority list first (``LIVE_VALIDATION_PRODUCT_PRIORITY`` / defaults), in order, deduplicated;
    then remaining runtime-allowed spot-like product ids from ``runtime_active_products``, deduplicated,
    tail sorted lexicographically for determinism.
    """
    act_set = {p.upper() for p in pol.runtime_active_products}
    seen: set[str] = set()
    out: List[str] = []
    for p in _nte_prod_policy.ordered_validation_candidates():
        u = p.strip().upper()
        if not u or u in seen:
            continue
        out.append(u)
        seen.add(u)
    for u in sorted(act_set):
        if u in seen:
            continue
        if is_spot_like_product_id(u):
            out.append(u)
            seen.add(u)
    return out


def _compute_groupings(
    attempts: List[Dict[str, Any]],
    *,
    validation_active: List[str],
) -> Dict[str, Any]:
    allowed_set = {p.upper() for p in validation_active}
    allowed_fundable: List[str] = []
    fundable_disallowed: List[str] = []
    allowed_unfundable: List[str] = []
    for row in attempts:
        pid = str(row.get("product_id") or "").upper()
        rr = str(row.get("rejection_reason") or "")
        qs = row.get("quote_sufficient")
        ra = row.get("runtime_allowed")
        if row.get("executable_now"):
            allowed_fundable.append(pid)
        elif qs is True and ra is False:
            fundable_disallowed.append(pid)
        elif pid in allowed_set and rr == "insufficient_quote_balance":
            allowed_unfundable.append(pid)
    return {
        "allowed_and_fundable_products": allowed_fundable,
        "fundable_but_runtime_disallowed_products": fundable_disallowed,
        "allowed_but_unfundable_products": allowed_unfundable,
    }


def _runtime_disallowed_products_with_reasons(pol: CoinbaseRuntimeProductPolicy) -> Dict[str, str]:
    """Products seen in default/priority universe that are not in ``load_nte_settings().products``."""
    allow = {p.upper() for p in pol.runtime_active_products}
    keys: List[str] = []
    for k in _nte_prod_policy.ordered_validation_candidates():
        keys.append(k.upper())
    for k in pol.configured_default_products:
        keys.append(k.upper())
    out: Dict[str, str] = {}
    for u in dict.fromkeys(keys):
        if u and u not in allow:
            out[u] = "not_in_load_nte_settings_products_allowlist"
    return dict(sorted(out.items()))


def _operator_plain_english_sentence(
    err_code: Optional[str],
    attempts: List[Dict[str, Any]],
) -> str:
    if err_code == "runtime_policy_disallows_fundable_product":
        for row in attempts:
            if row.get("quote_sufficient") and row.get("runtime_allowed") is False:
                qa = row.get("quote_asset")
                pid = row.get("product_id")
                return (
                    f"{pid} had sufficient {qa} funding, but runtime policy disallows that product "
                    f"(runtime_policy_disallows_fundable_product — check NTE_PRODUCTS / load_nte_settings)."
                )
        return (
            "Quote funding exists for at least one product the runtime policy blocks — "
            "see candidate_attempts for fundable_but_disallowed rows."
        )
    if err_code == "insufficient_allowed_quote_balance":
        return (
            "No runtime-allowed single-leg spot pair had sufficient quote balance for the requested notional "
            "(including venue minimum per product and per-quote wallets)."
        )
    if err_code == "venue_min_notional_not_fundable":
        return (
            "At least one runtime-allowed pair had enough quote to cover the requested notional alone, "
            "but not enough to satisfy max(requested_notional, venue_min_notional_usd) — venue minimum is binding."
        )
    if err_code == "runtime_policy_empty_or_invalid":
        return "No runtime-allowed products remain after env override — fix NTE_PRODUCTS."
    if err_code == "no_allowed_validation_product_found":
        return (
            "Validation never started because no eligible allowed pair could be funded "
            "and tradable after venue, policy, balance, and ticker checks."
        )
    return ""


def _build_quote_capital_truth(
    *,
    bal_by_ccy: Dict[str, float],
    pol: CoinbaseRuntimeProductPolicy,
    attempts: List[Dict[str, Any]],
    resolution_mode: str,
    chosen: Optional[str],
    requested_notional: float,
    blocked_reason: Optional[str],
) -> Dict[str, Any]:
    grp = _compute_groupings(attempts, validation_active=pol.validation_active_products)
    exec_by_product: Dict[str, float] = {}
    for row in attempts:
        pid = str(row.get("product_id") or "").upper()
        if pid:
            exec_by_product[pid] = float(row.get("available_quote_balance") or 0.0)
    total_fundable = sum(1 for a in attempts if a.get("quote_sufficient"))
    blocked_fundable = sum(
        1 for a in attempts if a.get("quote_sufficient") and not a.get("runtime_allowed")
    )
    fd = grp["fundable_but_runtime_disallowed_products"]
    return {
        "balances_by_currency": dict(bal_by_ccy),
        "quote_balances_by_currency": dict(bal_by_ccy),
        "required_notional": float(requested_notional),
        "blocked_reason": blocked_reason,
        "executable_quote_balances_by_allowed_product": exec_by_product,
        "total_fundable_quote_routes": total_fundable,
        "blocked_fundable_quote_routes": blocked_fundable,
        "preferred_validation_routes": list(_nte_prod_policy.ordered_validation_candidates()),
        "quote_wallet_constraints": {
            "scope": "single_leg_spot_only",
            "note": "Balances are per quote currency; multi-hop conversion is not applied.",
        },
        "allowed_and_fundable_products": grp["allowed_and_fundable_products"],
        "fundable_but_runtime_disallowed_products": fd,
        "fundable_but_disallowed_products": fd,
        "allowed_but_unfundable_products": grp["allowed_but_unfundable_products"],
        "chosen_product_id": chosen,
        "chosen_product": chosen,
        "resolution_mode": resolution_mode,
        "multi_leg_routing_honest_status": "search_only_not_execution_enabled",
        "multi_leg_route_execution_enabled": False,
        "multi_leg_execution_blocked_reason": "not_enabled_in_production",
        "routing_engine_scope": {
            "single_leg_direct": True,
            "multi_leg_exchange_execution": False,
            "multi_leg_graph_search": "diagnostics_only",
        },
    }


def _assemble_capital_artifacts(
    client: Any,
    bal_by_ccy: Dict[str, float],
    pol: CoinbaseRuntimeProductPolicy,
    attempts: List[Dict[str, Any]],
    *,
    chosen: Optional[str],
    resolution_status: str,
    err_code: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    all_bal = _cb_acct.get_spendable_balances_by_currency_all(client)
    univ = build_universal_runtime_policy(pol)
    pt_total: Optional[float] = None
    try:
        from trading_ai.nte.execution.routing.core.portfolio_truth import build_portfolio_truth_coinbase

        snap = build_portfolio_truth_coinbase(client)
        pt_total = float(snap.total_marked_usd)
        marks = {r.currency.upper(): r.mark_usd for r in snap.rows if r.mark_usd is not None}
        rows_norm = normalized_wallet_rows_from_balances(
            quote_balances=dict(bal_by_ccy),
            all_balances=all_bal,
            mark_by_asset={k: float(v) for k, v in marks.items()},
        )
    except Exception:
        rows_norm = normalized_wallet_rows_from_balances(
            quote_balances=dict(bal_by_ccy),
            all_balances=all_bal,
            mark_by_asset=None,
        )
        pt_total = None
    pt_snap = build_portfolio_truth_snapshot_dict(
        rows=rows_norm,
        total_marked_usd=float(pt_total or 0.0),
    )
    dcr = build_deployable_capital_report(
        bal_by_quote=dict(bal_by_ccy),
        all_balances=all_bal,
        pol=pol,
        universal=univ,
        attempts=attempts,
        chosen_product_id=chosen,
        resolution_status=resolution_status,
        error_code=err_code,
        portfolio_total_mark_value_usd=pt_total,
    )
    rrep = build_route_selection_report(
        pol=pol,
        universal=univ,
        attempts=attempts,
        chosen_product_id=chosen,
        all_balances=all_bal,
        error_code=err_code,
    )
    dcr["convertible_route_opportunities"] = list(
        rrep.get("multi_leg_route_search", {}).get("sample_paths_non_quote_to_quote") or []
    )
    summ = dcr.get("policy_vs_capital_summary") or {}
    dcr["policy_vs_capital_one_liner"] = policy_vs_capital_one_liner(
        error_code=err_code,
        fundable_disallowed=list(summ.get("fundable_but_runtime_disallowed_products") or []),
        allowed_unfundable=list(summ.get("allowed_but_unfundable_products") or []),
    )
    return dcr, rrep, pt_snap, univ.to_dict()


def _venue_min_binding_refinement(attempts: List[Dict[str, Any]], needed: float) -> Optional[str]:
    """
    When quote is insufficient only because ``max(requested_notional, venue_min)`` exceeds balance,
    but balance would have covered ``requested_notional`` alone — classify as venue-min binding.
    """
    needed = float(needed)
    for a in attempts:
        if not (a.get("runtime_allowed") and a.get("venue_supported")):
            continue
        if str(a.get("rejection_reason") or "") != "insufficient_quote_balance":
            continue
        try:
            qavail = float(a.get("available_quote_balance") or 0.0)
            req_quote = float(a.get("quote_required") or a.get("quote_required_for_attempt") or 0.0)
        except (TypeError, ValueError):
            continue
        if req_quote <= 0:
            continue
        if qavail + 1e-9 >= needed and qavail + 1e-9 < req_quote:
            return "venue_min_notional_not_fundable"
    return None


def _funding_truth_classification(
    attempts: List[Dict[str, Any]],
    *,
    needed: float,
    error_code: Optional[str],
) -> Dict[str, Any]:
    """Explicit multi-wallet / policy lens — direct quote spendable vs convertible route-search (honest)."""
    by_quote: Dict[str, float] = {}
    for a in attempts:
        qa = str(a.get("quote_asset") or "").upper()
        if qa:
            by_quote[qa] = float(a.get("available_quote_balance") or 0.0)
    return {
        "requested_notional": float(needed),
        "error_code": error_code,
        "quote_wallets_seen": by_quote,
        "direct_single_leg_spendable_truth": (
            "Balances are per quote currency for direct single-leg spot only — "
            "not total portfolio mark converted to instant spendable USDC/USD."
        ),
        "convertible_route_search_truth": (
            "Multi-leg conversion execution is not live — route_selection_report may list search-only paths."
        ),
        "currencies_not_blurred": ["USD", "USDC", "USDT", "EUR", "GBP"],
        "primary_failure_layer": "pre_execution_validation_preflight",
        "downstream_proof_is_not_primary_cause": True,
    }


def _determine_blocked_error(attempts: List[Dict[str, Any]]) -> Tuple[str, str, str]:
    """
    Returns ``(error_code, human_message, operator_next_step)`` for blocked resolutions.

    Error codes: ``insufficient_allowed_quote_balance``,
    ``runtime_policy_disallows_fundable_product``, ``no_runtime_supported_validation_product``,
    ``no_allowed_validation_product_found`` (fallback).
    """
    any_quote_sufficient = any(bool(a.get("quote_sufficient")) for a in attempts)
    policy_fundable = [
        a
        for a in attempts
        if a.get("runtime_allowed") is False and bool(a.get("quote_sufficient"))
    ]
    allowed_insufficient = [
        a
        for a in attempts
        if a.get("runtime_allowed") is True
        and str(a.get("rejection_reason") or "") == "insufficient_quote_balance"
    ]
    allowed_rows = [a for a in attempts if a.get("runtime_allowed") is True]
    venue_only_failures = bool(allowed_rows) and all(
        str(a.get("rejection_reason") or "") == "venue_catalog_missing_or_offline" for a in allowed_rows
    )
    ticker_only_failures = [
        a
        for a in attempts
        if a.get("venue_supported") is True
        and a.get("runtime_allowed") is True
        and a.get("quote_sufficient") is True
        and str(a.get("rejection_reason") or "") == "venue_ticker_unhealthy_or_missing"
    ]

    if policy_fundable and allowed_insufficient:
        return (
            "runtime_policy_disallows_fundable_product",
            "Quote balance exists for a product that is not runtime-allowed; allowed products lack quote.",
            "Add the fundable product to NTE_PRODUCTS (or remove env override that excludes it), "
            "or fund the quote currency for an allowed product.",
        )

    if not any_quote_sufficient:
        return (
            "insufficient_allowed_quote_balance",
            "No validation candidate has sufficient balance in its required quote currency for "
            "max(requested_notional, venue_min_notional_usd) per product.",
            "Deposit or convert so at least one runtime-allowed spot pair's quote wallet covers "
            "the required quote (including venue minimum when higher than requested notional).",
        )

    if venue_only_failures:
        return (
            "no_runtime_supported_validation_product",
            "Runtime-allowed products are missing from the venue catalog or marked offline.",
            "Verify Coinbase product ids and public catalog reachability; check effective_disallowed in policy snapshot.",
        )

    if ticker_only_failures:
        return (
            "no_runtime_supported_validation_product",
            "Runtime-allowed funded candidates failed public ticker / tradability checks.",
            "Verify Coinbase public ticker reachability and product tradability for the chosen pairs.",
        )

    return (
        "no_allowed_validation_product_found",
        "No single-leg spot candidate satisfied venue support, runtime allowlist, quote sufficiency "
        "(including venue minimum), and public ticker checks.",
        "See candidate_attempts for per-product rejection_reason; verify catalog, NTE allowlist, balances, and tickers.",
    )


def resolve_validation_product_coherent(
    client: Any,
    *,
    quote_notional: float,
    preferred_product_id: str = "BTC-USD",
    include_policy_snapshot: bool = True,
    write_control_artifacts: bool = False,
    runtime_root: Optional[Path] = None,
) -> ValidationResolution:
    """
    Single-leg spot validation resolution. **Multi-leg routing is not implemented** — diagnostics state this explicitly.

    Returns **either** success with ``chosen_product_id`` **or** blocked with ``error_code`` —
    never a misleading placeholder product id on failure.
    """
    bal_by_ccy = _cb_acct.get_quote_balances_by_currency(client)
    needed = float(quote_notional)
    attempts: List[Dict[str, Any]] = []

    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=True)
    policy_ref_root = Path(runtime_root or ezras_runtime_root()).resolve()
    canonical_policy_ref = str(policy_ref_root / "data" / "control" / "runtime_policy_snapshot.json")
    if not pol.runtime_allowlist_valid:
        ec = pol.runtime_allowlist_error_code or "runtime_policy_empty_or_invalid"
        om = pol.runtime_allowlist_operator_message or "No runtime-allowed products remain after env override"
        diag_empty: Dict[str, Any] = {
            "resolution_version": "coherent_v6",
            "routing_truth": {
                "single_leg_spot_only": True,
                "multi_leg_exchange_routing": "not_implemented",
            },
            "quote_balances_by_currency": dict(bal_by_ccy),
            "quote_balances": dict(bal_by_ccy),
            "canonical_runtime_policy": pol.to_dict() if include_policy_snapshot else None,
            "runtime_allowed_products": list(pol.effective_products),
            "runtime_disallowed_products": _runtime_disallowed_products_with_reasons(pol),
            "ordered_candidates": [],
            "candidate_evaluations": [],
            "candidate_attempts": [],
            "final_selection_reason": f"blocked:{ec}",
            "resolution_status": "blocked",
            "error_code": ec,
            "chosen_product_id": None,
            "operator_next_step": om,
            "error_message_human": om,
            "operator_message_plain_english": om,
            "failure_layer": "runtime_allowlist_configuration",
            "canonical_runtime_policy_reference": canonical_policy_ref,
        }
        qct = _build_quote_capital_truth(
            bal_by_ccy=bal_by_ccy,
            pol=pol,
            attempts=[],
            resolution_mode="single_leg_blocked",
            chosen=None,
            requested_notional=needed,
            blocked_reason=ec,
        )
        diag_empty["quote_capital_truth"] = qct
        dcr_e, rrep_e, pt_e, univ_e = _assemble_capital_artifacts(
            client,
            bal_by_ccy,
            pol,
            [],
            chosen=None,
            resolution_status="blocked",
            err_code=ec,
        )
        diag_empty["canonical_universal_runtime_policy"] = univ_e
        diag_empty["deployable_capital_report"] = dcr_e
        diag_empty["route_selection_report"] = rrep_e
        diag_empty["portfolio_truth_snapshot"] = pt_e
        diag_empty["validation_preflight"] = {
            "direct_fundable_products": [],
            "convertible_fundable_targets": rrep_e.get("multi_leg_route_search", {}).get(
                "sample_paths_non_quote_to_quote"
            ),
            "chosen_route": {"kind": "blocked", "product_id": None},
            "chosen_product_if_single_leg": None,
            "required_quote": None,
            "portfolio_truth_snapshot_ref": "data/control/portfolio_truth_snapshot.json",
            "root_block_reason": ec,
            "operator_next_step": om,
        }
        diag_empty["deployable_capital_summary"] = {
            "conservative_quote_usd_plus_usdc": dcr_e.get("conservative_deployable_capital"),
            "portfolio_total_mark_value_usd": dcr_e.get("portfolio_total_mark_value_usd"),
        }
        diag_empty["policy_vs_capital_summary"] = dcr_e.get("policy_vs_capital_summary")
        diag_empty["direct_vs_convertible_summary"] = dcr_e.get("direct_vs_convertible_summary")
        diag_empty["funding_truth_classification"] = _funding_truth_classification(
            [], needed=needed, error_code=ec
        )
        diag_empty["root_cause_primary"] = ec
        if write_control_artifacts or (
            (os.environ.get("EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS") or "").strip().lower()
            in ("1", "true", "yes")
        ):
            write_validation_control_artifacts(
                validation_diagnostics=diag_empty,
                quote_capital_truth=qct,
                runtime_root=runtime_root,
                deployable_capital_report=dcr_e,
                route_selection_report=rrep_e,
                portfolio_truth_snapshot=pt_e,
            )
            _after_validation_control_write(runtime_root)
        return ValidationResolution(
            resolution_status="blocked",
            chosen_product_id=None,
            error_code=ec,
            error_message=om,
            diagnostics=diag_empty,
        )

    candidates = merge_validation_candidates_for_runtime(pol)
    pref = (preferred_product_id or "BTC-USD").strip().upper()

    val_allowed_set = {p.upper() for p in pol.validation_active_products}
    chosen: Optional[str] = None
    preferred_candidate_reference: Optional[str] = None

    diag: Dict[str, Any] = {
        "resolution_version": "coherent_v6",
        "routing_truth": {
            "single_leg_spot_only": True,
            "multi_leg_exchange_routing": "not_implemented",
            "evaluation_class": "single_leg_direct",
        },
        "quote_balances_by_currency": dict(bal_by_ccy),
        "quote_balances": dict(bal_by_ccy),
        "quote_notional": needed,
        "preferred_product_id": pref,
        "requested_preferred_product_id": pref,
        "validation_product_priority": list(_nte_prod_policy.ordered_validation_candidates()),
        "ordered_candidates": list(candidates),
        "merged_validation_candidates": list(candidates),
        "nte_allowed_products": list(pol.runtime_active_products),
        "preferred_products_ordered": list(_nte_prod_policy.ordered_validation_candidates()),
        "quote_available_by_currency": dict(bal_by_ccy),
        "canonical_runtime_policy": pol.to_dict() if include_policy_snapshot else None,
        "proof_fields": {
            "runtime_active_products": list(pol.runtime_active_products),
            "validation_active_products": list(pol.validation_active_products),
            "execution_active_products": list(pol.execution_active_products),
            "env_override_products": pol.env_override_products,
            "effective_products_source": pol.effective_products_source,
            "products_removed_by_env": pol.products_removed_by_env,
            "products_added_by_env": pol.products_added_by_env,
        },
        "nte_runtime_active_products": list(pol.runtime_active_products),
        "validation_allowed_products": list(pol.validation_allowed_products),
        "execution_allowed_products": list(pol.execution_allowed_products),
        "runtime_allowed_products": list(pol.effective_products),
        "runtime_disallowed_products": _runtime_disallowed_products_with_reasons(pol),
        "failure_layer": None,
        "canonical_runtime_policy_reference": canonical_policy_ref,
    }

    for priority_rank, pid in enumerate(candidates, start=1):
        pku = pid.upper()
        base_a, quote_a = parse_spot_base_quote(pid)
        vmin = float(venue_min_notional_usd(pid))
        required_quote = max(needed, vmin)
        sup_venue = product_supported_by_runtime_venue_catalog(pid, pol)
        allowed_runtime = _nte_prod_policy.coinbase_product_nte_allowed(pid)
        allowed_val = pku in val_allowed_set
        qavail = float(bal_by_ccy.get(quote_a, 0.0))
        notion_ok = qavail >= required_quote
        row: Dict[str, Any] = {
            "product_id": pid,
            "priority_rank": priority_rank,
            "base_asset": base_a,
            "quote_asset": quote_a,
            "venue_min_notional_usd": vmin,
            "required_quote_notional": needed,
            "quote_required": float(required_quote),
            "quote_required_for_attempt": float(required_quote),
            "available_quote_balance": qavail,
            "quote_available": qavail,
            "quote_available_by_currency": {quote_a: qavail},
            "quote_sufficient": notion_ok,
            "runtime_allowed": allowed_runtime,
            "venue_supported": sup_venue,
            "ticker_ok": None,
            "executable_now": False,
            "rejection_reason": None,
            "allowed_by_validation_policy": allowed_val,
            "allowed_by_runtime_policy": allowed_runtime,
            "supported_by_venue": sup_venue,
            "quote_notional_required": needed,
            "quote_notional_satisfied": notion_ok,
            "quote_asset_required": quote_a,
            "quote_asset_available": qavail,
            "health_ok": None,
            "status": "pending",
            "detail": "",
            "final_status": "pending",
            "final_rejection_reason": None,
            "reason_code": None,
            "failure_layer": None,
        }

        def _log_attempt() -> None:
            logger.info(
                "validation_product_candidate product_id=%s priority_rank=%s venue_supported=%s "
                "runtime_allowed=%s quote_required=%s quote_available=%s quote_sufficient=%s "
                "ticker_ok=%s rejection_reason=%s",
                row.get("product_id"),
                row.get("priority_rank"),
                row.get("venue_supported"),
                row.get("runtime_allowed"),
                row.get("quote_required"),
                row.get("quote_available"),
                row.get("quote_sufficient"),
                row.get("ticker_ok"),
                row.get("rejection_reason"),
            )

        if not sup_venue:
            row["final_status"] = "rejected"
            row["status"] = "rejected"
            rr = "venue_catalog_missing_or_offline"
            row["rejection_reason"] = rr
            row["final_rejection_reason"] = rr
            row["reason_code"] = rr
            row["failure_layer"] = "venue_catalog"
            row["detail"] = "Product missing from venue catalog snapshot or marked offline for this runtime id."
            attempts.append(row)
            _log_attempt()
            continue

        if not allowed_runtime:
            row["final_status"] = "rejected"
            row["status"] = "rejected"
            rr = "runtime_policy_disallows_product"
            row["rejection_reason"] = rr
            row["final_rejection_reason"] = rr
            row["reason_code"] = "validation_product_not_in_allowlist"
            row["failure_layer"] = "runtime_allow_policy"
            row["detail"] = f"Not in load_nte_settings().products; need {required_quote} {quote_a} for min+request."
            if notion_ok and preferred_candidate_reference is None:
                preferred_candidate_reference = pid
            attempts.append(row)
            _log_attempt()
            continue

        if not notion_ok:
            row["final_status"] = "rejected"
            row["status"] = "rejected"
            rr = "insufficient_quote_balance"
            row["rejection_reason"] = rr
            row["final_rejection_reason"] = rr
            row["reason_code"] = "insufficient_quote_for_required_notional_and_venue_min"
            row["failure_layer"] = "balance"
            row["balance_detail"] = (
                f"need_{required_quote}_requested_{needed}_vmin_{vmin}_have_{qavail}_{quote_a}"
            )
            row["detail"] = (
                f"Quote wallet {quote_a} balance {qavail} < required {required_quote} "
                f"(max(requested_notional={needed}, venue_min={vmin}))."
            )
            attempts.append(row)
            _log_attempt()
            continue

        ok_tick = _cb_acct._product_spot_tradable_public(pid)
        row["ticker_ok"] = ok_tick
        row["health_ok"] = ok_tick
        if not ok_tick:
            row["final_status"] = "rejected"
            row["status"] = "rejected"
            rr = "venue_ticker_unhealthy_or_missing"
            row["rejection_reason"] = rr
            row["final_rejection_reason"] = rr
            row["reason_code"] = "validation_product_not_tradable_or_no_ticker"
            row["failure_layer"] = "venue_health"
            row["detail"] = "Public ticker missing or unhealthy for this product_id."
            attempts.append(row)
            _log_attempt()
            continue

        row["final_status"] = "selected"
        row["status"] = "selected"
        row["executable_now"] = True
        row["rejection_reason"] = None
        row["reason_code"] = "selected"
        row["detail"] = (
            f"First priority candidate passing venue, runtime allowlist, quote "
            f"({required_quote} {quote_a} required), and ticker."
        )
        chosen = pid
        attempts.append(row)
        _log_attempt()
        break

    diag["candidate_evaluations"] = attempts
    diag["candidate_attempts"] = attempts
    grp = _compute_groupings(attempts, validation_active=pol.validation_active_products)
    diag["allowed_and_fundable_products"] = grp["allowed_and_fundable_products"]
    diag["fundable_but_runtime_disallowed_products"] = grp["fundable_but_runtime_disallowed_products"]
    diag["allowed_but_unfundable_products"] = grp["allowed_but_unfundable_products"]
    diag["funding_vs_policy_lens"] = {
        "fundable_but_runtime_disallowed": grp["fundable_but_runtime_disallowed_products"],
        "allowed_but_unfundable": grp["allowed_but_unfundable_products"],
        "allowed_and_fundable": grp["allowed_and_fundable_products"],
        "supported_but_unhealthy_ticker": [
            str(x.get("product_id"))
            for x in attempts
            if str(x.get("rejection_reason") or "") == "venue_ticker_unhealthy_or_missing"
        ],
    }
    if preferred_candidate_reference:
        diag["preferred_candidate_reference"] = preferred_candidate_reference

    if chosen:
        aligned = bool(_nte_prod_policy.coinbase_product_nte_allowed(chosen))
        sel_reason = (
            f"selected:{chosen}:first_in_ordered_candidates_passing_"
            f"venue_supported_and_runtime_allowed_and_quote_sufficient_and_ticker_ok"
        )
        diag["chosen_reason"] = sel_reason
        diag["final_selection_reason"] = sel_reason
        diag["selector_aligned_with_guard"] = aligned
        diag["failure_layer"] = None
        diag["resolution_status"] = "success"
        diag["error_code"] = None
        diag["chosen_product_id"] = chosen
        diag["operator_next_step"] = "Proceed with validation using chosen_product_id."
        diag["operator_message_plain_english"] = (
            f"Using {chosen}: allowed by load_nte_settings().products and quote-funded for this notional."
        )
        qct = _build_quote_capital_truth(
            bal_by_ccy=bal_by_ccy,
            pol=pol,
            attempts=attempts,
            resolution_mode="single_leg_direct",
            chosen=chosen,
            requested_notional=needed,
            blocked_reason=None,
        )
        diag["quote_capital_truth"] = qct
        _, qq_chosen = parse_spot_base_quote(str(chosen))
        dcr_s, rrep_s, pt_s, univ_s = _assemble_capital_artifacts(
            client,
            bal_by_ccy,
            pol,
            attempts,
            chosen=chosen,
            resolution_status="success",
            err_code=None,
        )
        diag["canonical_universal_runtime_policy"] = univ_s
        diag["deployable_capital_report"] = dcr_s
        diag["route_selection_report"] = rrep_s
        diag["portfolio_truth_snapshot"] = pt_s
        diag["deployable_capital_summary"] = {
            "conservative_quote_usd_plus_usdc": dcr_s.get("conservative_deployable_capital"),
            "portfolio_total_mark_value_usd": dcr_s.get("portfolio_total_mark_value_usd"),
        }
        diag["policy_vs_capital_summary"] = dcr_s.get("policy_vs_capital_summary")
        diag["direct_vs_convertible_summary"] = dcr_s.get("direct_vs_convertible_summary")
        diag["funding_truth_classification"] = _funding_truth_classification(
            attempts, needed=needed, error_code=None
        )
        diag["root_cause_primary"] = None
        diag["validation_preflight"] = {
            "direct_fundable_products": grp["allowed_and_fundable_products"],
            "convertible_fundable_targets": rrep_s.get("multi_leg_route_search", {}).get(
                "sample_paths_non_quote_to_quote"
            ),
            "chosen_route": {"kind": "single_leg_spot", "product_id": chosen, "legs": [chosen]},
            "chosen_product_if_single_leg": chosen,
            "required_quote": qq_chosen,
            "portfolio_truth_snapshot_ref": "data/control/portfolio_truth_snapshot.json",
            "root_block_reason": None,
            "operator_next_step": diag["operator_next_step"],
        }
        if write_control_artifacts or (
            (os.environ.get("EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS") or "").strip().lower()
            in ("1", "true", "yes")
        ):
            write_validation_control_artifacts(
                validation_diagnostics=diag,
                quote_capital_truth=qct,
                runtime_root=runtime_root,
                deployable_capital_report=dcr_s,
                route_selection_report=rrep_s,
                portfolio_truth_snapshot=pt_s,
            )
            _after_validation_control_write(runtime_root)
        return ValidationResolution(
            resolution_status="success",
            chosen_product_id=chosen,
            error_code=None,
            error_message=None,
            diagnostics=diag,
        )

    err, human, nxt = _determine_blocked_error(attempts)
    if err == "insufficient_allowed_quote_balance":
        vref = _venue_min_binding_refinement(attempts, needed)
        if vref == "venue_min_notional_not_fundable":
            err = vref
            human = (
                "Venue minimum notional exceeds available quote for at least one allowed product "
                "(requested notional alone could be covered, but max(requested, venue_min) is not)."
            )
            nxt = (
                "Top up the relevant quote wallet to satisfy max(requested_notional, venue_min_notional_usd), "
                "or reduce requested notional if compatible with venue rules."
            )
    plain = _operator_plain_english_sentence(err, attempts)
    diag["chosen_reason"] = err
    diag["final_selection_reason"] = f"blocked:{err}"
    diag["failure_layer"] = "validation_preflight"
    diag["resolution_status"] = "blocked"
    diag["error_code"] = err
    diag["chosen_product_id"] = None
    diag["operator_next_step"] = nxt
    diag["error_message_human"] = human if not plain else f"{human} {plain}".strip()
    diag["operator_message_plain_english"] = plain or human
    qct = _build_quote_capital_truth(
        bal_by_ccy=bal_by_ccy,
        pol=pol,
        attempts=attempts,
        resolution_mode="single_leg_blocked",
        chosen=None,
        requested_notional=needed,
        blocked_reason=err,
    )
    diag["quote_capital_truth"] = qct
    diag["selector_aligned_with_guard"] = False
    dcr_b, rrep_b, pt_b, univ_b = _assemble_capital_artifacts(
        client,
        bal_by_ccy,
        pol,
        attempts,
        chosen=None,
        resolution_status="blocked",
        err_code=err,
    )
    diag["canonical_universal_runtime_policy"] = univ_b
    diag["deployable_capital_report"] = dcr_b
    diag["route_selection_report"] = rrep_b
    diag["portfolio_truth_snapshot"] = pt_b
    diag["deployable_capital_summary"] = {
        "conservative_quote_usd_plus_usdc": dcr_b.get("conservative_deployable_capital"),
        "portfolio_total_mark_value_usd": dcr_b.get("portfolio_total_mark_value_usd"),
    }
    diag["policy_vs_capital_summary"] = dcr_b.get("policy_vs_capital_summary")
    diag["direct_vs_convertible_summary"] = dcr_b.get("direct_vs_convertible_summary")
    diag["funding_truth_classification"] = _funding_truth_classification(
        attempts, needed=needed, error_code=err
    )
    diag["root_cause_primary"] = err
    diag["validation_preflight"] = {
        "direct_fundable_products": grp["allowed_and_fundable_products"],
        "convertible_fundable_targets": rrep_b.get("multi_leg_route_search", {}).get(
            "sample_paths_non_quote_to_quote"
        ),
        "chosen_route": {"kind": "blocked", "product_id": None, "legs": []},
        "chosen_product_if_single_leg": None,
        "required_quote": None,
        "portfolio_truth_snapshot_ref": "data/control/portfolio_truth_snapshot.json",
        "root_block_reason": err,
        "operator_next_step": nxt,
    }
    if write_control_artifacts or (
        (os.environ.get("EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS") or "").strip().lower()
        in ("1", "true", "yes")
    ):
        write_validation_control_artifacts(
            validation_diagnostics=diag,
            quote_capital_truth=qct,
            runtime_root=runtime_root,
            deployable_capital_report=dcr_b,
            route_selection_report=rrep_b,
            portfolio_truth_snapshot=pt_b,
        )
        _after_validation_control_write(runtime_root)
    return ValidationResolution(
        resolution_status="blocked",
        chosen_product_id=None,
        error_code=err,
        error_message=human,
        diagnostics=diag,
    )


def _after_validation_control_write(runtime_root: Optional[Path]) -> None:
    try:
        from trading_ai.ratios.artifacts_writer import refresh_ratio_artifacts_after_validation

        refresh_ratio_artifacts_after_validation(runtime_root=runtime_root)
    except Exception:
        logger.debug("ratio artifact refresh after validation skipped", exc_info=True)


def assert_validation_resolution_execution_invariant(vr: ValidationResolution) -> None:
    """
    Hard invariant: blocked resolutions never carry a chosen product id; success always does.
    Multi-leg exchange execution is not live — diagnostics must not imply otherwise.
    """
    if vr.resolution_status == "success":
        if not (vr.chosen_product_id and str(vr.chosen_product_id).strip()):
            raise AssertionError("success resolution requires non-empty chosen_product_id")
        if vr.error_code is not None:
            raise AssertionError("success resolution must have error_code=None")
        return
    if vr.chosen_product_id is not None:
        raise AssertionError("blocked resolution must have chosen_product_id=None")
    if vr.error_code is None:
        raise AssertionError("blocked resolution requires error_code")


def tuple_for_legacy_api(vr: ValidationResolution) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    """``(chosen_product_id|None, diagnostics, error_code|None)`` for older call sites."""
    assert_validation_resolution_execution_invariant(vr)
    if vr.resolution_status == "success":
        return vr.chosen_product_id, vr.diagnostics, None
    return None, vr.diagnostics, vr.error_code
