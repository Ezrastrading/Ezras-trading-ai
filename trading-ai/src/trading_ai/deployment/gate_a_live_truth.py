"""
Gate A (Coinbase / NTE) — honest live vs validation vs advisory classification.

Read-only — does not place orders or change sizing.
"""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.nte.execution.routing.integration.gate_hooks import gate_ratio_and_reserve_bundle


def gate_a_live_truth_snapshot() -> Dict[str, Any]:
    """
    What is validation-proven vs advisory-only for Avenue A / Gate A.

    ``ratio_reserve`` is **advisory_runtime_context** — not an order router.
    """
    rr: Dict[str, Any] = {}
    try:
        rr = gate_ratio_and_reserve_bundle(write_ratio_artifacts=False)
    except Exception as exc:
        rr = {"error": str(exc), "honest_note": "gate_ratio_and_reserve_bundle_unavailable"}

    return {
        "gate": "A",
        "avenue": "coinbase_nte",
        "classification": {
            "what_is_live": "Coinbase order paths when NTE guard + governance + env allow live execution",
            "what_is_validation_proven": "Micro-validation streak + validation preflight when credentials and write flags allow artifacts",
            "what_is_advisory_only": [
                "ratio_policy_snapshot / gate ratio views (read-first, not order-enforced)",
                "reserve_capital_report without prior deployable_capital_report.json (insufficient source truth)",
            ],
            "ratio_framework_role": "runtime_readable_not_order_enforced",
        },
        "what_blocks_first_20_reference": "See final_readiness.json critical_blockers — authoritative list",
        "ratio_reserve_read": rr,
    }
