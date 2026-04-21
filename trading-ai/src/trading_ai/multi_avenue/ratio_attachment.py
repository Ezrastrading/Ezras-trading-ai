"""Document how universal ratio/reserve patterns attach per scope — read-only metadata."""

from __future__ import annotations

from typing import Any, Dict


def ratio_reserve_attachment_metadata(*, avenue_id: str, gate_id: str) -> Dict[str, Any]:
    """
    Advisory metadata for operators — actual reads still go through ``gate_ratio_and_reserve_bundle``.

    Does not create per-avenue ratio files automatically; overlays remain keyed by gate in registry.
    """
    return {
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "read_first_entrypoint": "trading_ai.nte.execution.routing.integration.gate_hooks.gate_ratio_and_reserve_bundle",
        "classification": "advisory_runtime_context_not_order_enforced",
        "contamination_note": "Never mix one avenue's venue balances into another's reserve interpretation.",
    }
