"""Post–round-trip rebuy eligibility (governance + adaptive + avenue state)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class RebuyEvaluation:
    rebuy_allowed: bool
    reason_codes: List[str]
    next_candidate_source: str
    blocked_by_adaptive: bool
    blocked_by_governance: bool
    avenue_cooling: bool
    duplicate_or_lockout: bool
    detail: Dict[str, Any]


def evaluate_rebuy_eligibility(
    *,
    prior_round_trip_finalized: bool,
    logging_succeeded: bool,
    reconciliation_ok_or_classified: bool,
    governance_recheck_ok: bool,
    adaptive_recheck_ok: bool,
    failsafe_halted: bool,
    duplicate_would_block: bool,
    avenue_cooldown_active: bool,
) -> RebuyEvaluation:
    """
    Rebuy is denied unless the prior chain completed and governance/adaptive re-pass.

    Policy: logging must succeed first; reconciliation must be OK or explicitly classified (not silent).
    """
    reasons: List[str] = []
    if not prior_round_trip_finalized:
        reasons.append("prior_round_trip_not_finalized")
    if not logging_succeeded:
        reasons.append("logging_required_before_rebuy")
    if not reconciliation_ok_or_classified:
        reasons.append("reconciliation_pending_or_failed")
    if not governance_recheck_ok:
        reasons.append("governance_recheck_failed")
    if not adaptive_recheck_ok:
        reasons.append("adaptive_brake_active")
    if failsafe_halted:
        reasons.append("failsafe_halted")
    if duplicate_would_block:
        reasons.append("duplicate_guard_would_block")
    if avenue_cooldown_active:
        reasons.append("avenue_cooldown")

    ok = len(reasons) == 0
    return RebuyEvaluation(
        rebuy_allowed=ok,
        reason_codes=reasons,
        next_candidate_source="scan_rank_pipeline" if ok else "none_until_unblocked",
        blocked_by_adaptive=not adaptive_recheck_ok,
        blocked_by_governance=not governance_recheck_ok,
        avenue_cooling=avenue_cooldown_active,
        duplicate_or_lockout=duplicate_would_block or failsafe_halted,
        detail={
            "policy": "rebuy_requires_logging_then_governance_adaptive",
            "prior_round_trip_finalized": prior_round_trip_finalized,
            "logging_succeeded": logging_succeeded,
            "reconciliation_ok_or_classified": reconciliation_ok_or_classified,
        },
    )
