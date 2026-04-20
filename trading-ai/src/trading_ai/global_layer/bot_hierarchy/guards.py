"""Hard guards — hierarchy layer never grants live authority or substitutes for proof artifacts."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.global_layer.bot_hierarchy.models import HierarchyBotRecord, GATE_CANDIDATE_STAGE_ORDER


def assert_hierarchy_bot_no_live_authority(bot: Dict[str, Any] | HierarchyBotRecord) -> None:
    raw = bot.model_dump() if isinstance(bot, HierarchyBotRecord) else dict(bot)
    lp = raw.get("live_permissions") or {}
    if any(bool(lp.get(k)) for k in ("venue_orders", "runtime_switch", "capital_allocation_mutate")):
        raise ValueError("hierarchy_guard:live_permissions_must_remain_false")
    if raw.get("can_modify_live_logic"):
        raise ValueError("hierarchy_guard:can_modify_live_logic_forbidden")


def manager_report_is_not_runtime_proof(report: Dict[str, Any]) -> bool:
    """Structured reports are advisory unless backed by the existing proof / promotion artifact chain."""
    return not bool(report.get("is_runtime_proof"))


def assert_no_stage_skip(from_stage: str, to_stage: str) -> None:
    a = GATE_CANDIDATE_STAGE_ORDER.index(from_stage)
    b = GATE_CANDIDATE_STAGE_ORDER.index(to_stage)
    if b != a + 1 and b != a:
        raise ValueError(f"hierarchy_guard:stage_skip_forbidden:{from_stage}->{to_stage}")
