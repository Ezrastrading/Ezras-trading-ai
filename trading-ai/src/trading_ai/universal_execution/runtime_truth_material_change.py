"""
Bridge: material state changes → dependency-based artifact refresh (no clock-only refresh).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


def refresh_runtime_truth_after_material_change(
    *,
    reason: str = "material_change",
    runtime_root: Optional[Path] = None,
    force: bool = False,
    include_advisory: bool = True,
) -> Dict[str, Any]:
    """
    After live micro validation, production tick, round-trip, mode transition, governance ack, etc.

    Delegates to :func:`run_refresh_runtime_artifacts` so staleness is dependency-fingerprint based.
    """
    from trading_ai.reports.runtime_artifact_refresh_manager import run_refresh_runtime_artifacts

    out = run_refresh_runtime_artifacts(
        runtime_root=runtime_root,
        force=force,
        show_stale_only=False,
        include_advisory=include_advisory,
        print_final_switch_truth=False,
    )
    out["refresh_runtime_truth_reason"] = reason
    try:
        from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle

        out["live_switch_closure"] = write_live_switch_closure_bundle(
            runtime_root=runtime_root,
            trigger_surface=reason,
            reason=reason,
        )
    except Exception as exc:
        out["live_switch_closure"] = {"error": str(exc)}
    return out
