"""Reserve vs deployable — numeric layer on top of deployable_capital_report (when present)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.ratios.universal_ratio_registry import RatioPolicyBundle


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def build_reserve_capital_report(
    *,
    bundle: RatioPolicyBundle,
    control_dir: Path,
) -> Dict[str, Any]:
    """
    Uses ``deployable_capital_report.json`` if present; otherwise honest partials.
    """
    dpath = control_dir / "deployable_capital_report.json"
    dcr = _read_json(dpath) if dpath.is_file() else None
    if dcr is None:
        dcr = {}
    deployable_truth_ok = bool(dcr) and any(
        dcr.get(k) is not None
        for k in (
            "conservative_deployable_capital",
            "validation_deployable_capital",
            "live_execution_deployable_capital",
        )
    )
    source_truth_status = "sufficient" if deployable_truth_ok else "insufficient_source_truth"
    cons = float(dcr.get("conservative_deployable_capital") or 0.0)
    live = float(dcr.get("live_execution_deployable_capital") or cons)
    val = float(dcr.get("validation_deployable_capital") or cons)
    port = dcr.get("portfolio_total_mark_value_usd")

    hr = float(bundle.universal_ratios.get("universal.hard_reserve_ratio", {}).get("value") or 0.05)
    sr = float(bundle.universal_ratios.get("universal.soft_reserve_ratio", {}).get("value") or 0.02)

    hard_amt = cons * hr
    soft_amt = cons * sr
    reserved = hard_amt + soft_amt
    deploy_after_reserve = max(0.0, cons - reserved)

    return {
        "artifact": "reserve_capital_report",
        "version": "v1",
        "source_truth_status": source_truth_status,
        "source_deployable_report": str(dpath) if dpath.is_file() else None,
        "total_portfolio_mark_value_usd": port,
        "directly_deployable_capital": dcr.get("direct_quote_balances_by_asset"),
        "conservative_deployable_capital": cons,
        "validation_deployable_capital": val,
        "live_execution_deployable_capital": live,
        "hard_reserve_capital": hard_amt,
        "soft_reserve_capital": soft_amt,
        "risk_buffer_capital": soft_amt,
        "reserved_capital_total": reserved,
        "deployable_after_reserves": deploy_after_reserve,
        "convertible_capital_note": dcr.get("direct_vs_convertible_summary"),
        "venue_specific_reserve_requirements": {
            "coinbase": "See avenue.coinbase.reserve_buffer_ratio in ratio_policy_snapshot",
        },
        "gate_specific_reserve_requirements": {
            "gate_a": "Uses NTE caps; reserve ratios apply before sizing.",
            "gate_b": "Momentum path may further shrink effective deployable in scanner.",
        },
        "emergency_reserve_buffer": hard_amt,
        "interpretation": {
            "not_all_mark_value_is_deployable": True,
            "reserve_computed_from_conservative_slice": deployable_truth_ok,
            "insufficient_source_truth": not deployable_truth_ok,
            "insufficient_source_truth_note": (
                None
                if deployable_truth_ok
                else "deployable_capital_report.json missing or has no deployable capital fields; "
                "numeric reserve lines below are ratio-scaffold only, not grounded in venue truth."
            ),
        },
    }
