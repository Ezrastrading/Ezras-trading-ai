"""Strict handoff envelope between bots — schema validation only (no network I/O)."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

REQUIRED_KEYS = ("handoff_id", "from_bot_id", "to_bot_id", "input_ref", "output_schema", "timeout_sec", "retry_policy", "rejection_rule")


def validate_handoff_envelope(env: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    for k in REQUIRED_KEYS:
        if k not in env or env.get(k) in (None, ""):
            errs.append(f"missing_or_empty:{k}")
    try:
        if int(env.get("timeout_sec") or 0) <= 0:
            errs.append("invalid_timeout_sec")
    except (TypeError, ValueError):
        errs.append("invalid_timeout_sec")
    rp = env.get("retry_policy")
    if not isinstance(rp, dict):
        errs.append("retry_policy_must_be_object")
    else:
        if "max_attempts" not in rp:
            errs.append("retry_policy.max_attempts")
    return len(errs) == 0, errs
