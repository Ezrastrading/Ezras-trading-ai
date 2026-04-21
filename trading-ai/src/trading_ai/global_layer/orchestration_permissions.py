"""Hard permission checks — shadow/advisory cannot place live orders; execution_authority is unique slot."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from trading_ai.global_layer.execution_authority import get_holder
from trading_ai.global_layer.orchestration_schema import PermissionLevel, permission_allows_live_orders


def bot_may_place_live_orders(bot: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Live venue orders: denied unless permission_level is execution_authority AND slot matches
    execution_authority.json for (avenue, gate, route). This preserves single authority path.
    """
    level = str(bot.get("permission_level") or PermissionLevel.OBSERVE_ONLY.value)
    if not permission_allows_live_orders(level):
        return False, "permission_level_denies_live_orders"
    avenue = str(bot.get("avenue") or "")
    gate = str(bot.get("gate") or "none")
    route = str(bot.get("route") or "default")
    bid = str(bot.get("bot_id") or "")
    holder = get_holder(avenue, gate, route)
    if not holder:
        return False, "no_execution_authority_slot_granted"
    if str(holder.get("bot_id")) != bid:
        return False, "not_canonical_holder_for_slot"
    return True, "ok"


def bot_may_shadow_simulate(bot: Dict[str, Any]) -> bool:
    from trading_ai.global_layer.orchestration_schema import permission_allows_shadow_simulation

    level = str(bot.get("permission_level") or "")
    return permission_allows_shadow_simulation(level)


def assert_no_self_promotion(old_level: str, new_level: str, actor_bot_id: str, subject_bot_id: str) -> None:
    if actor_bot_id == subject_bot_id and new_level != old_level:
        raise ValueError("self_promotion_forbidden")
