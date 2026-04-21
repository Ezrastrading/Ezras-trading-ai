"""Evidence-honest status transitions — no upgrades without supporting sources."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from trading_ai.intelligence.edge_research.models import ResearchStatus


# Map target status -> which proving artifact path prefixes satisfy a *potential* upgrade (operator still reviews).
STATUS_REQUIRES_ARTIFACT_HINTS: Dict[str, List[str]] = {
    ResearchStatus.mock_supported.value: [
        "data/control/mock_execution_harness_results.json",
        "data/control/execution_friction_lab.json",
    ],
    ResearchStatus.staged_supported.value: [
        "data/control/gate_b_staged_validation.json",
        "data/control/prelive_reality_matrix.json",
        "data/control/sizing_calibration_report.json",
    ],
    ResearchStatus.live_supported.value: [
        "data/control/honest_live_status_matrix.json",
        "data/control/portfolio_truth_snapshot.json",
    ],
}


def can_upgrade_status(
    current: ResearchStatus | str,
    proposed: ResearchStatus | str,
    *,
    evidence_paths: List[str],
    explicit_live_confirmation: bool = False,
) -> Tuple[bool, str]:
    """
    Block impossible upgrades: e.g. mock -> live without any live-context artifact.

    This is conservative: live_supported additionally requires explicit confirmation flag
    so we never infer live from file presence alone.
    """
    cur = current.value if isinstance(current, ResearchStatus) else str(current)
    prop = proposed.value if isinstance(proposed, ResearchStatus) else str(proposed)
    order = [
        ResearchStatus.rejected.value,
        ResearchStatus.archived.value,
        ResearchStatus.hypothesis.value,
        ResearchStatus.under_research.value,
        ResearchStatus.degraded.value,
        ResearchStatus.mock_supported.value,
        ResearchStatus.staged_supported.value,
        ResearchStatus.live_supported.value,
    ]
    if prop not in order or cur not in order:
        return False, "unknown_status"
    if order.index(prop) < order.index(cur):
        return True, "downgrade_or_lateral_allowed"
    if order.index(prop) == order.index(cur):
        return True, "no_change"

    ev_set = {str(p) for p in evidence_paths}
    if prop == ResearchStatus.mock_supported.value:
        if any(h in ev_set for h in STATUS_REQUIRES_ARTIFACT_HINTS[ResearchStatus.mock_supported.value]):
            return True, "mock_evidence_present"
        return False, "missing_mock_class_artifact"

    if prop == ResearchStatus.staged_supported.value:
        hints = STATUS_REQUIRES_ARTIFACT_HINTS[ResearchStatus.staged_supported.value]
        if any(h in ev_set for h in hints):
            return True, "staged_evidence_present"
        return False, "missing_staged_class_artifact"

    if prop == ResearchStatus.live_supported.value:
        if not explicit_live_confirmation:
            return False, "live_requires_explicit_operator_confirmation"
        if any(h in ev_set for h in STATUS_REQUIRES_ARTIFACT_HINTS[ResearchStatus.live_supported.value]):
            return True, "live_evidence_present"
        return False, "missing_live_class_artifact"

    return True, "ok"


def apply_status_if_allowed(
    row: Dict[str, Any],
    proposed: ResearchStatus | str,
    *,
    evidence_paths: Optional[List[str]] = None,
    explicit_live_confirmation: bool = False,
) -> Dict[str, Any]:
    """Return updated row dict or original if blocked."""
    paths = evidence_paths if evidence_paths is not None else list(row.get("supporting_artifact_paths") or [])
    cur = row.get("current_status") or ResearchStatus.hypothesis.value
    ok, _reason = can_upgrade_status(
        cur,
        proposed,
        evidence_paths=paths,
        explicit_live_confirmation=explicit_live_confirmation,
    )
    if not ok:
        return dict(row)
    out = dict(row)
    out["current_status"] = proposed.value if isinstance(proposed, ResearchStatus) else str(proposed)
    return out
