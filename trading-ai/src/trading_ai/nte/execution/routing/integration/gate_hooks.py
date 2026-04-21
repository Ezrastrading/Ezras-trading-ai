"""Gate A / Gate B shared hooks — portfolio truth + policy snapshot (no duplicate logic)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from trading_ai.nte.execution.routing.core.portfolio_truth import (
    PortfolioTruthSnapshot,
    build_portfolio_truth_coinbase,
)
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import (
    resolve_coinbase_runtime_product_policy,
    write_runtime_policy_artifacts,
)
from trading_ai.shark.outlets.coinbase import CoinbaseClient


def gate_ratio_and_reserve_bundle(
    *,
    runtime_root: Path | None = None,
    write_ratio_artifacts: bool = False,
) -> Dict[str, Any]:
    """
    Universal ratio views for Gate A / B (read-only). Optionally materialize ratio JSON files.

    Does not place orders or change live sizing by itself.
    """
    from trading_ai.ratios.artifacts_writer import write_all_ratio_artifacts
    from trading_ai.ratios.gate_ratio_access import gate_a_ratio_view, gate_b_ratio_view
    from trading_ai.runtime_paths import ezras_runtime_root

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    artifact_paths: Dict[str, Any] = {}
    if write_ratio_artifacts:
        artifact_paths = write_all_ratio_artifacts(runtime_root=root, append_change_log=False)
    return {
        "gate_a_ratio_view": gate_a_ratio_view(runtime_root=root),
        "gate_b_ratio_view": gate_b_ratio_view(runtime_root=root),
        "artifact_paths": artifact_paths,
        "expected_paths": {
            "ratio_policy_snapshot": str(root / "data" / "control" / "ratio_policy_snapshot.json"),
            "reserve_capital_report": str(root / "data" / "control" / "reserve_capital_report.json"),
            "deployable_capital_report": str(root / "data" / "control" / "deployable_capital_report.json"),
        },
        "honest_note": "Reserve numbers require deployable_capital_report.json from validation/micro-validation.",
    }


def gate_shared_portfolio_and_policy_bundle(
    *,
    runtime_root: Path | None = None,
) -> Dict[str, Any]:
    """
    Single bundle for Gate A / Gate B: runtime policy + portfolio truth (read-only).

    Does not place orders.
    """
    client = CoinbaseClient()
    pt: PortfolioTruthSnapshot = build_portfolio_truth_coinbase(client)
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=True)
    arts = write_runtime_policy_artifacts(runtime_root=runtime_root, include_venue_catalog=True)
    return {
        "runtime_policy": pol.to_dict(),
        "portfolio_truth": {
            "total_marked_usd": pt.total_marked_usd,
            "liquid_quote_usd": pt.liquid_quote_usd,
            "rows": [
                {
                    "currency": r.currency,
                    "available": r.available,
                    "mark_usd": r.mark_usd,
                    "dust": r.dust,
                }
                for r in pt.rows
            ],
            "notes": pt.notes,
        },
        "artifacts": arts,
    }
