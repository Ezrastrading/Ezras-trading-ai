"""Rebuy audit — universal contract during first-20 (append-only counter updates)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.first_20.constants import P_REBUY_AUDIT, default_rebuy_audit
from trading_ai.first_20.storage import read_json, write_json


def load_audit(runtime_root: Optional[Any] = None) -> Dict[str, Any]:
    doc = read_json(P_REBUY_AUDIT, runtime_root=runtime_root)
    if not isinstance(doc, dict):
        return default_rebuy_audit()
    base = default_rebuy_audit()
    base.update(doc)
    return base


def save_audit(doc: Dict[str, Any], runtime_root: Optional[Any] = None) -> None:
    write_json(P_REBUY_AUDIT, doc, runtime_root=runtime_root)


def record_rebuy_evaluation(
    *,
    rebuy_allowed: bool,
    block_reason: Optional[str],
    any_attempt: bool = False,
    any_before_log_completion: bool = False,
    any_before_exit_truth: bool = False,
    runtime_root: Optional[Any] = None,
) -> Dict[str, Any]:
    """Call from loop proof or orchestration when a rebuy decision is evaluated."""
    a = load_audit(runtime_root=runtime_root)
    if any_attempt:
        a["rebuy_attempts"] = int(a.get("rebuy_attempts") or 0) + 1
    if rebuy_allowed:
        a["rebuy_allowed_count"] = int(a.get("rebuy_allowed_count") or 0) + 1
    else:
        a["rebuy_blocked_count"] = int(a.get("rebuy_blocked_count") or 0) + 1
        br = str(block_reason or "blocked")
        reasons = dict(a.get("rebuy_block_reasons") or {})
        reasons[br] = int(reasons.get(br) or 0) + 1
        a["rebuy_block_reasons"] = reasons
    if any_before_log_completion:
        a["any_rebuy_before_log_completion"] = True
    if any_before_exit_truth:
        a["any_rebuy_before_exit_truth"] = True
    clean = not a.get("any_rebuy_before_log_completion") and not a.get("any_rebuy_before_exit_truth")
    a["rebuy_contract_clean"] = clean
    save_audit(a, runtime_root=runtime_root)
    return a


def merge_rebuy_into_truth_pause(audit: Dict[str, Any], phase: str) -> str:
    """If contract broken, caller must set PAUSED_REVIEW_REQUIRED."""
    if audit.get("any_rebuy_before_log_completion") or audit.get("any_rebuy_before_exit_truth"):
        from trading_ai.first_20.constants import PhaseStatus

        return PhaseStatus.PAUSED_REVIEW_REQUIRED.value
    return phase
