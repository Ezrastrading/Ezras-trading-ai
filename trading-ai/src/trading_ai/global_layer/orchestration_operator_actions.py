"""Explicit operator actions — quarantine, disable, unfreeze paths (auditable registry patches)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.audit_trail import append_audit_event
from trading_ai.global_layer.bot_registry import get_bot, patch_bot
from trading_ai.global_layer.bot_types import BotLifecycleState
from trading_ai.global_layer.orchestration_schema import OrchestrationBotStatus, PermissionLevel


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def quarantine_bot(
    bot_id: str,
    *,
    reason: str,
    registry_path: Optional[Path] = None,
    operator: str = "operator",
) -> Dict[str, Any]:
    """Freeze bot, strip live permissions, mark degraded — does not delete registry rows."""
    b = get_bot(bot_id, path=registry_path)
    if not b:
        return {"ok": False, "error": f"unknown_bot:{bot_id}"}
    patch_bot(
        bot_id,
        {
            "lifecycle_state": BotLifecycleState.FROZEN.value,
            "permission_level": PermissionLevel.OBSERVE_ONLY.value,
            "status": OrchestrationBotStatus.DEGRADED.value,
            "disable_reason": f"quarantine:{reason}",
            "quarantine_at": _iso(),
            "quarantine_by": operator,
        },
        path=registry_path,
    )
    append_audit_event(
        "bot_quarantine",
        {"bot_id": bot_id, "reason": reason},
        bot_id="OPERATOR",
        approved_by=operator,
        evidence_refs=[],
    )
    return {"ok": True, "bot_id": bot_id, "state": "quarantined"}


def disable_bot(
    bot_id: str,
    *,
    reason: str,
    registry_path: Optional[Path] = None,
    operator: str = "operator",
) -> Dict[str, Any]:
    b = get_bot(bot_id, path=registry_path)
    if not b:
        return {"ok": False, "error": f"unknown_bot:{bot_id}"}
    patch_bot(
        bot_id,
        {
            "lifecycle_state": BotLifecycleState.DEGRADED.value,
            "permission_level": PermissionLevel.OBSERVE_ONLY.value,
            "status": OrchestrationBotStatus.DISABLED.value,
            "disable_reason": f"disabled:{reason}",
            "disabled_at": _iso(),
            "disabled_by": operator,
        },
        path=registry_path,
    )
    append_audit_event(
        "bot_disable",
        {"bot_id": bot_id, "reason": reason},
        bot_id="OPERATOR",
        approved_by=operator,
        evidence_refs=[],
    )
    return {"ok": True, "bot_id": bot_id, "state": "disabled"}


def list_bot_summaries(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    from trading_ai.global_layer.bot_registry import load_registry

    reg = load_registry(registry_path)
    rows = []
    for b in reg.get("bots") or []:
        rows.append(
            {
                "bot_id": b.get("bot_id"),
                "avenue": b.get("avenue"),
                "gate": b.get("gate"),
                "role": b.get("role"),
                "lifecycle_state": b.get("lifecycle_state"),
                "permission_level": b.get("permission_level"),
                "status": b.get("status"),
                "last_heartbeat_at": b.get("last_heartbeat_at"),
            }
        )
    return {"truth_version": "bot_list_summary_v1", "generated_at": _iso(), "bots": rows}
