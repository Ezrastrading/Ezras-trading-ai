"""Task routing — primary/backup assignment and escalation (deterministic; no venue orders)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import get_bots_by_avenue, load_registry
from trading_ai.global_layer.bot_types import BotRole, TaskStatus
from trading_ai.global_layer.task_registry import append_task, canonical_task_template


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pick_primary_bot(*, avenue: str, role: str, gate: str = "none") -> Optional[str]:
    """Choose first active-like bot for avenue+gate+role."""
    bots = get_bots_by_avenue(avenue)
    for b in bots:
        if str(b.get("role")) != role:
            continue
        if str(b.get("gate")) != gate:
            continue
        st = str(b.get("lifecycle_state") or "")
        if st in ("active", "promoted", "probation", "shadow"):
            return str(b.get("bot_id"))
    return None


def route_task_shadow(
    *,
    avenue: str,
    gate: str,
    task_type: str,
    source_bot_id: str,
    role: str,
    evidence_ref: str,
) -> Dict[str, Any]:
    """
    Phase-3 style: record routing intent only. Does not call execution.
    """
    primary = pick_primary_bot(avenue=avenue, role=role, gate=gate)
    if not primary:
        primary = f"unassigned_{role.lower()}_{avenue}"
    t = canonical_task_template(
        avenue=avenue,
        gate=gate,
        task_type=task_type,
        source_bot_id=source_bot_id,
        assigned_bot_id=primary,
        evidence_ref=evidence_ref,
    )
    t["routing_mode"] = "shadow"
    t["status"] = TaskStatus.ASSIGNED.value
    return append_task(t)


def escalation_path(bot_id: str) -> List[str]:
    reg = load_registry()
    for b in reg.get("bots") or []:
        if str(b.get("bot_id")) == bot_id:
            role = str(b.get("role") or "")
            from trading_ai.global_layer.bot_permissions import default_permission_matrix

            m = default_permission_matrix().get(role) or {}
            return list(m.get("escalation_targets") or ["CEO"])
    return ["CEO"]


def mark_escalated(task_id: str, *, reason: str, path_store=None) -> Dict[str, Any]:
    # JSONL is append-only audit: emit new row with same task_id + escalated
    row = {
        "task_id": task_id,
        "status": TaskStatus.ESCALATED.value,
        "escalation_reason": reason,
        "escalated_at": _iso(),
    }
    return append_task(row, path=path_store)
