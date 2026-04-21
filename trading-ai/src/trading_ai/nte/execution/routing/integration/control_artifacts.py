"""Write operator-facing control artifacts for validation + quote capital (single-leg scope)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.deployment.paths import control_data_dir as _control_data_dir_base
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import write_runtime_policy_artifacts


def routing_data_dir_for_root(runtime_root: Optional[Path] = None) -> Path:
    if runtime_root:
        root = Path(runtime_root).resolve()
        p = root / "data" / "routing"
        p.mkdir(parents=True, exist_ok=True)
        return p
    root = ezras_runtime_root().resolve()
    p = root / "data" / "routing"
    p.mkdir(parents=True, exist_ok=True)
    return p


def control_data_dir_for_root(runtime_root: Optional[Path] = None) -> Path:
    if runtime_root:
        root = Path(runtime_root).resolve()
        p = root / "data" / "control"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return _control_data_dir_base()


def write_validation_control_artifacts(
    *,
    validation_diagnostics: Dict[str, Any],
    quote_capital_truth: Dict[str, Any],
    runtime_root: Optional[Path] = None,
    deployable_capital_report: Optional[Dict[str, Any]] = None,
    route_selection_report: Optional[Dict[str, Any]] = None,
    portfolio_truth_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Writes validation + quote capital + optional deployable/route/portfolio artifacts under ``data/control/`` and mirrors under ``data/routing/``."""
    rp = write_runtime_policy_artifacts(runtime_root=runtime_root, include_venue_catalog=True)
    cdir = control_data_dir_for_root(runtime_root=runtime_root)
    rdir = routing_data_dir_for_root(runtime_root=runtime_root)
    rep_j = cdir / "validation_product_resolution_report.json"
    rep_t = cdir / "validation_product_resolution_report.txt"
    qc_j = cdir / "quote_capital_truth.json"
    qc_t = cdir / "quote_capital_truth.txt"

    rep_payload = {
        "artifact": "validation_product_resolution_report",
        "version": "v2",
        "resolution_status": validation_diagnostics.get("resolution_status"),
        "chosen_product_id": validation_diagnostics.get("chosen_product_id"),
        "error_code": validation_diagnostics.get("error_code"),
        "root_cause_primary": validation_diagnostics.get("root_cause_primary"),
        "funding_truth_classification": validation_diagnostics.get("funding_truth_classification"),
        "error_message_human": validation_diagnostics.get("error_message_human"),
        "operator_next_step": validation_diagnostics.get("operator_next_step"),
        "routing_truth": validation_diagnostics.get("routing_truth"),
        "ordered_candidates": validation_diagnostics.get("ordered_candidates")
        or validation_diagnostics.get("merged_validation_candidates"),
        "candidate_attempts": validation_diagnostics.get("candidate_attempts")
        or validation_diagnostics.get("candidate_evaluations"),
        "final_selection_reason": validation_diagnostics.get("final_selection_reason"),
        "selector_aligned_with_guard": validation_diagnostics.get("selector_aligned_with_guard"),
        "canonical_runtime_policy_reference": validation_diagnostics.get("canonical_runtime_policy_reference"),
        "canonical_runtime_policy": validation_diagnostics.get("canonical_runtime_policy"),
        "diagnostics": validation_diagnostics,
    }
    rep_j.write_text(json.dumps(rep_payload, indent=2, default=str), encoding="utf-8")
    err = validation_diagnostics.get("error_code")
    chosen = validation_diagnostics.get("chosen_product_id")
    lines = [
        "VALIDATION PRODUCT RESOLUTION (single-leg spot)",
        "===============================================",
        f"resolution_status: {validation_diagnostics.get('resolution_status')}",
        f"chosen_product_id: {chosen}",
        f"error_code: {err}",
        f"operator_next_step: {validation_diagnostics.get('operator_next_step', '')}",
        "",
        "candidate_evaluations (summary):",
    ]
    for row in (
        validation_diagnostics.get("candidate_attempts")
        or validation_diagnostics.get("candidate_evaluations")
        or []
    ):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"  {row.get('product_id')}: status={row.get('status')} "
            f"venue={row.get('venue_supported')} runtime_ok={row.get('runtime_allowed')} "
            f"quote_sufficient={row.get('quote_sufficient')} ticker_ok={row.get('ticker_ok')} "
            f"reason={row.get('reason_code')!r} detail={str(row.get('detail') or '')[:120]}"
        )
    rep_t.write_text("\n".join(lines) + "\n", encoding="utf-8")

    qc_j.write_text(json.dumps(quote_capital_truth, indent=2, default=str), encoding="utf-8")
    qlines = [
        "QUOTE CAPITAL TRUTH (single-leg spot; multi-hop routing not implemented)",
        "=========================================================================",
        f"required_notional: {quote_capital_truth.get('required_notional')}",
        f"blocked_reason: {quote_capital_truth.get('blocked_reason')}",
        f"chosen_product_id: {quote_capital_truth.get('chosen_product_id')}",
        f"balances_by_currency: {quote_capital_truth.get('balances_by_currency')}",
        f"quote_balances_by_currency: {quote_capital_truth.get('quote_balances_by_currency')}",
        f"allowed_and_fundable_products: {quote_capital_truth.get('allowed_and_fundable_products')}",
        f"fundable_but_disallowed_products: {quote_capital_truth.get('fundable_but_disallowed_products')}",
        f"allowed_but_unfundable_products: {quote_capital_truth.get('allowed_but_unfundable_products')}",
        f"resolution_mode: {quote_capital_truth.get('resolution_mode')}",
        f"multi_leg_routing: {quote_capital_truth.get('multi_leg_routing_honest_status')}",
        f"multi_leg_execution_blocked_reason: {quote_capital_truth.get('multi_leg_execution_blocked_reason')}",
    ]
    qc_t.write_text("\n".join(qlines) + "\n", encoding="utf-8")

    out = {
        "validation_product_resolution_report_json": str(rep_j),
        "validation_product_resolution_report_txt": str(rep_t),
        "quote_capital_truth_json": str(qc_j),
        "quote_capital_truth_txt": str(qc_t),
    }
    out.update(rp)

    def _mirror_payload(name: str, payload: Dict[str, Any]) -> None:
        cj = cdir / f"{name}.json"
        ct = cdir / f"{name}.txt"
        rj = rdir / f"{name}.json"
        js = json.dumps(payload, indent=2, default=str)
        cj.write_text(js, encoding="utf-8")
        rj.write_text(js, encoding="utf-8")
        tlines = [name.upper(), "=" * len(name), js[:14000]]
        txt = "\n".join(tlines) + "\n"
        ct.write_text(txt, encoding="utf-8")
        (rdir / f"{name}.txt").write_text(txt, encoding="utf-8")
        out[f"{name}_json_control"] = str(cj)
        out[f"{name}_json_routing"] = str(rj)

    if deployable_capital_report:
        _mirror_payload("deployable_capital_report", deployable_capital_report)
    if route_selection_report:
        _mirror_payload("route_selection_report", route_selection_report)
    if portfolio_truth_snapshot:
        _mirror_payload("portfolio_truth_snapshot", portfolio_truth_snapshot)

    return out
