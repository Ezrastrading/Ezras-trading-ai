"""
Controlled bot creation — **only** through validated factory (bots cannot spawn bots).

Uses :mod:`orchestration_spawn_manager` caps, cooldown, and audit. New bots default to **promotion T0** (shadow band; see :func:`permission_and_capabilities_for_promotion_tier`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_containment import load_containment
from trading_ai.global_layer.bot_memory import ensure_bot_memory_files, ensure_orchestration_bot_memory_files
from trading_ai.global_layer.bot_policy import assert_not_live_execution_override, validate_bot_config
from trading_ai.global_layer.bot_registry import get_bots_by_avenue, register_bot
from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.budget_governor import can_allocate_bot_slot
from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch
from trading_ai.global_layer.orchestration_registry_normalize import normalize_bot_record
from trading_ai.global_layer.orchestration_spawn_manager import audit_spawn_decision, evaluate_spawn_policy

MIN_TRADES_FOR_SPECIALIZATION = 20

_ACTIVE_LIKE = frozenset(
    {
        BotLifecycleState.INITIALIZED.value,
        BotLifecycleState.SHADOW.value,
        BotLifecycleState.ELIGIBLE.value,
        BotLifecycleState.PROBATION.value,
        BotLifecycleState.ACTIVE.value,
        BotLifecycleState.PROMOTED.value,
        BotLifecycleState.DEGRADED.value,
    }
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_bot_if_needed(
    context: Dict[str, Any],
    *,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    context keys:
    - avenue, gate, role, version
    - performance_threshold_failed: bool
    - trade_count: int
    - measured_gap: bool (e.g. low utility)
    - prefer_improve_existing: bool (if True and bot exists, skip create)
    - spawn_reason (optional audit string)
    """
    ks = load_kill_switch()
    if ks.get("orchestration_frozen"):
        audit_spawn_decision(False, "orchestration_kill_switch", context)
        return {"created": False, "reason": "orchestration_kill_switch"}

    ctn = load_containment()
    if ctn.get("freeze_all_new_bots"):
        audit_spawn_decision(False, "containment_freeze_all", context)
        return {"created": False, "reason": "containment_freeze_all"}
    avenue = str(context.get("avenue") or "").strip()
    gate = str(context.get("gate") or "none")
    role = str(context.get("role") or "")
    if ctn.get("avenue_containment", {}).get(avenue):
        return {"created": False, "reason": "avenue_contained"}
    if ctn.get("gate_containment", {}).get(f"{avenue}:{gate}"):
        return {"created": False, "reason": "gate_contained"}

    if not context.get("performance_threshold_failed") and not context.get("measured_gap"):
        return {"created": False, "reason": "no_performance_signal"}
    if int(context.get("trade_count") or 0) < MIN_TRADES_FOR_SPECIALIZATION:
        return {"created": False, "reason": "insufficient_trade_data"}

    if context.get("prefer_improve_existing"):
        existing = [b for b in get_bots_by_avenue(avenue, path=registry_path) if str(b.get("role")) == role]
        if existing:
            return {"created": False, "reason": "improve_existing_first", "existing": [e.get("bot_id") for e in existing]}

    if role == BotRole.EXECUTION.value:
        for b in get_bots_by_avenue(avenue, path=registry_path):
            if str(b.get("role")) != BotRole.EXECUTION.value:
                continue
            if str(b.get("gate")) != gate:
                continue
            if str(b.get("lifecycle_state")) in _ACTIVE_LIKE:
                return {"created": False, "reason": "single_execution_bot_per_avenue_gate"}

    allow, why = can_allocate_bot_slot(avenue=avenue)
    if not allow:
        audit_spawn_decision(False, f"budget:{why}", context)
        return {"created": False, "reason": f"budget:{why}"}

    bot_id = f"bot_{uuid.uuid4().hex[:12]}"
    cfg: Dict[str, Any] = {
        "bot_id": bot_id,
        "role": role,
        "avenue": avenue,
        "gate": gate,
        "version": str(context.get("version") or "v0"),
        "lifecycle_state": BotLifecycleState.SHADOW.value,
        "spawn_reason": str(context.get("spawn_reason") or "create_bot_if_needed"),
        "spawn_source_bot_id": str(context.get("spawn_source_bot_id") or "bot_spawn_manager"),
        "created_at": _iso(),
        "performance": {},
    }
    assert_not_live_execution_override(cfg)
    ok, errs = validate_bot_config(cfg)
    if not ok:
        audit_spawn_decision(False, "validation_failed", {"errors": errs, **context})
        return {"created": False, "reason": "validation_failed", "errors": errs}

    normalized = normalize_bot_record(cfg)
    sp_ok, sp_why = evaluate_spawn_policy(normalized_bot=normalized, registry_path=registry_path)
    if not sp_ok:
        audit_spawn_decision(False, sp_why, context)
        return {"created": False, "reason": sp_why}

    register_bot(normalized, path=registry_path)
    ensure_bot_memory_files(bot_id)
    ensure_orchestration_bot_memory_files(bot_id)
    audit_spawn_decision(True, "spawned", {"bot_id": bot_id, **context})
    return {"created": True, "bot_id": bot_id, "config": normalized}


def create_specialization_request(
    *,
    avenue: str,
    gate: str,
    role: str,
    justification: str,
    evidence_refs: List[str],
) -> Dict[str, Any]:
    req = {
        "request_id": f"spec_{uuid.uuid4().hex[:12]}",
        "avenue": avenue,
        "gate": gate,
        "role": role,
        "justification": justification,
        "evidence_refs": evidence_refs,
        "status": "pending",
        "created_at": _iso(),
    }
    from trading_ai.global_layer.audit_trail import append_audit_event

    append_audit_event(
        "specialization_request",
        {"request": req},
        bot_id="CEO",
        approved_by=None,
        evidence_refs=evidence_refs,
    )
    return req


def approve_specialization_request(request: Dict[str, Any], approver: str) -> Dict[str, Any]:
    out = dict(request)
    out["status"] = "approved"
    out["approved_by"] = approver
    out["approved_at"] = _iso()
    ctx = {
        "avenue": request["avenue"],
        "gate": request["gate"],
        "role": request["role"],
        "version": "v1",
        "performance_threshold_failed": True,
        "trade_count": MIN_TRADES_FOR_SPECIALIZATION,
        "measured_gap": True,
        "prefer_improve_existing": False,
        "spawn_reason": "ceo_approved_specialization",
        "spawn_source_bot_id": "CEO",
    }
    created = create_bot_if_needed(ctx)
    out["creation_result"] = created
    return out


def reject_specialization_request(request: Dict[str, Any], approver: str, reason: str) -> Dict[str, Any]:
    out = dict(request)
    out["status"] = "rejected"
    out["rejected_by"] = approver
    out["rejected_at"] = _iso()
    out["reject_reason"] = reason
    return out
