"""
Operator-governed AI authority levels. Execution-changing actions require explicit approval.

Never self-promotes: level changes are operator-driven (artifacts + env), not inferred from code.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root

LEVEL_1_ANALYZE_ONLY = 1
LEVEL_2_PROPOSE_CHANGES = 2
LEVEL_3_OPERATOR_APPROVED_LIMITED_ADJUST = 3
LEVEL_4_NOT_AVAILABLE_UNTIL_EXPLICITLY_ENABLED = 4

# Back-compat alias used in earlier prompt drafts
try:
    AI_AUTHORITY_LEVEL = int((os.environ.get("AI_AUTHORITY_LEVEL") or "1").strip() or "1")
except ValueError:
    AI_AUTHORITY_LEVEL = 1


def authority_state_path(runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = root / "data" / "control" / "ai_authority_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_authority_state(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    p = authority_state_path(runtime_root=runtime_root)
    if not p.is_file():
        return default_authority_state(runtime_root=runtime_root)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else default_authority_state(runtime_root=runtime_root)
    except (OSError, json.JSONDecodeError):
        return default_authority_state(runtime_root=runtime_root)


def default_authority_state(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    lvl = max(LEVEL_1_ANALYZE_ONLY, min(LEVEL_4_NOT_AVAILABLE_UNTIL_EXPLICITLY_ENABLED, AI_AUTHORITY_LEVEL))
    return {
        "version": 1,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "effective_level": lvl,
        "level_names": {
            "1": "LEVEL_1_ANALYZE_ONLY",
            "2": "LEVEL_2_PROPOSE_CHANGES",
            "3": "LEVEL_3_OPERATOR_APPROVED_LIMITED_ADJUST",
            "4": "LEVEL_4_NOT_AVAILABLE_UNTIL_EXPLICITLY_ENABLED",
        },
        "operator_is_final_authority": True,
        "self_promotion_allowed": False,
        "notes": (
            "Levels are not upgraded automatically. Set AI_AUTHORITY_LEVEL env and/or edit this file "
            "with operator intent. Level 4 is reserved — not for autonomous trading."
        ),
    }


def save_authority_state(payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> Path:
    p = authority_state_path(runtime_root=runtime_root)
    body = {**payload, "updated_at_utc": datetime.now(timezone.utc).isoformat()}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def effective_level(*, runtime_root: Optional[Path] = None) -> int:
    st = load_authority_state(runtime_root=runtime_root)
    try:
        return int(st.get("effective_level") or AI_AUTHORITY_LEVEL)
    except (TypeError, ValueError):
        return LEVEL_1_ANALYZE_ONLY


def can_change_strategy(*, runtime_root: Optional[Path] = None) -> bool:
    return effective_level(runtime_root=runtime_root) >= LEVEL_3_OPERATOR_APPROVED_LIMITED_ADJUST


def can_deploy_without_operator(*, runtime_root: Optional[Path] = None) -> bool:
    """Hard false at level < 3; at 3+ still requires caps — caller must enforce."""
    return effective_level(runtime_root=runtime_root) >= LEVEL_3_OPERATOR_APPROVED_LIMITED_ADJUST


def execution_reasoning_keys() -> List[str]:
    return ["decision_reasoning", "expected_outcome", "risk_assessment", "confidence_level"]


def reasoning_payload_complete(reasoning: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(reasoning, dict):
        return False
    for k in execution_reasoning_keys():
        v = reasoning.get(k)
        if v is None:
            return False
        s = str(v).strip()
        if len(s) < 4:
            return False
    return True


def block_execution_without_reasoning(
    reasoning: Optional[Dict[str, Any]],
) -> Tuple[bool, str]:
    """
    Returns (blocked, reason). When REQUIRE_AI_EXECUTION_REASONING is unset/false, never blocks
    (caller may still attach derived reasoning for traceability).
    """
    req = (os.environ.get("REQUIRE_AI_EXECUTION_REASONING") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not req:
        return False, "require_ai_execution_reasoning_disabled"
    if reasoning_payload_complete(reasoning):
        return False, "ok"
    return True, "missing_or_unclear_execution_reasoning"


def ai_reasoning_gate_for_nt_entry(
    *,
    product_id: str,
    derived_reasoning: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Gate for NTE Coinbase entry path. Respects REQUIRE_AI_EXECUTION_REASONING.
    """
    blocked, reason = block_execution_without_reasoning(derived_reasoning)
    if blocked:
        return False, reason
    return True, f"ok product={product_id}"


def weekly_proposal_envelope(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """All auto-generated strategy/risk suggestions must carry this status."""
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "proposal_only_not_executed",
        "operator_approval_required": True,
        "items": items,
    }
