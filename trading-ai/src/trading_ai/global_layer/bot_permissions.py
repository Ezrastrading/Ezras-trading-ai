"""Minimum permissions per role — execution tightly restricted; learning cannot mutate live execution here."""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Tuple

from trading_ai.global_layer.bot_types import BotRole

# Sets of string permission tokens (auditable, deterministic).
READ_RUNTIME_TRUTH = "read_runtime_truth"
READ_SHARED_TRUTH = "read_shared_truth"
READ_MARKET_DATA = "read_market_data"
WRITE_SIGNAL = "write_signal"
WRITE_TASK = "write_task"
WRITE_LOCAL_MEMORY = "write_local_memory"
WRITE_AUDIT = "write_audit"
RECOMMEND_EXECUTION = "recommend_execution"
SUBMIT_EXECUTION_INTENT = "submit_execution_intent"  # still must pass pipeline + risk
ENFORCE_RISK = "enforce_risk"
POST_TRADE_ANALYSIS = "post_trade_analysis"
PROPOSE_LESSON = "propose_lesson"
APPROVE_SHARED_LESSON = "approve_shared_lesson"

FORBID_DIRECT_ORDER = "forbid_direct_venue_order"
FORBID_MUTATE_GLOBAL_TRUTH = "forbid_mutate_global_truth"
FORBID_SPAWN_BOT = "forbid_spawn_bot"


def default_permission_matrix() -> Dict[str, Dict[str, FrozenSet[str]]]:
    """role -> {read, write, allowed_actions, forbidden_actions}."""
    common_forbid = frozenset({FORBID_DIRECT_ORDER, FORBID_SPAWN_BOT})
    return {
        BotRole.SCANNER.value: {
            "read_permissions": frozenset(
                {READ_MARKET_DATA, READ_RUNTIME_TRUTH, READ_SHARED_TRUTH, WRITE_LOCAL_MEMORY}
            ),
            "write_permissions": frozenset({WRITE_SIGNAL, WRITE_TASK, WRITE_AUDIT}),
            "allowed_actions": frozenset({"emit_scan", "enqueue_task"}),
            "forbidden_actions": common_forbid | frozenset({SUBMIT_EXECUTION_INTENT, RECOMMEND_EXECUTION}),
            "escalation_targets": frozenset({"DECISION", "RISK", "CEO"}),
        },
        BotRole.DECISION.value: {
            "read_permissions": frozenset({READ_SHARED_TRUTH, READ_MARKET_DATA, READ_RUNTIME_TRUTH, WRITE_LOCAL_MEMORY}),
            "write_permissions": frozenset({WRITE_TASK, WRITE_AUDIT}),
            "allowed_actions": frozenset({"rank_candidates", "recommend"}),
            "forbidden_actions": common_forbid
            | frozenset({FORBID_MUTATE_GLOBAL_TRUTH, SUBMIT_EXECUTION_INTENT}),
            "escalation_targets": frozenset({"EXECUTION", "RISK", "CEO"}),
        },
        BotRole.EXECUTION.value: {
            "read_permissions": frozenset({READ_SHARED_TRUTH, READ_RUNTIME_TRUTH}),
            "write_permissions": frozenset({WRITE_AUDIT}),
            "allowed_actions": frozenset({"submit_intent_through_pipeline"}),
            "forbidden_actions": frozenset({FORBID_SPAWN_BOT}),
            "escalation_targets": frozenset({"RISK", "CEO"}),
        },
        BotRole.RISK.value: {
            "read_permissions": frozenset({READ_SHARED_TRUTH, READ_RUNTIME_TRUTH}),
            "write_permissions": frozenset({WRITE_AUDIT, WRITE_TASK}),
            "allowed_actions": frozenset({ENFORCE_RISK, "veto", "resize"}),
            "forbidden_actions": common_forbid | frozenset({WRITE_SIGNAL}),
            "escalation_targets": frozenset({"EXECUTION", "CEO"}),
        },
        BotRole.LEARNING.value: {
            "read_permissions": frozenset({READ_SHARED_TRUTH, READ_RUNTIME_TRUTH, WRITE_LOCAL_MEMORY}),
            "write_permissions": frozenset({WRITE_LOCAL_MEMORY, PROPOSE_LESSON, WRITE_AUDIT}),
            "allowed_actions": frozenset({POST_TRADE_ANALYSIS, PROPOSE_LESSON}),
            "forbidden_actions": common_forbid
            | frozenset({SUBMIT_EXECUTION_INTENT, FORBID_MUTATE_GLOBAL_TRUTH, APPROVE_SHARED_LESSON}),
            "escalation_targets": frozenset({"CEO"}),
        },
    }


def permissions_for_bot(bot: Dict[str, Any]) -> Dict[str, Any]:
    role = str(bot.get("role") or "")
    m = default_permission_matrix().get(role)
    if not m:
        return {
            "read_permissions": [],
            "write_permissions": [],
            "allowed_actions": [],
            "forbidden_actions": list(frozenset({FORBID_DIRECT_ORDER, FORBID_SPAWN_BOT})),
            "escalation_targets": ["CEO"],
        }
    return {
        "read_permissions": sorted(m["read_permissions"]),
        "write_permissions": sorted(m["write_permissions"]),
        "allowed_actions": sorted(m["allowed_actions"]),
        "forbidden_actions": sorted(m["forbidden_actions"]),
        "escalation_targets": sorted(m["escalation_targets"]),
    }


def action_allowed(bot: Dict[str, Any], action: str) -> Tuple[bool, str]:
    role = str(bot.get("role") or "")
    m = default_permission_matrix().get(role)
    if not m:
        return False, "unknown_role"
    a = str(action).strip()
    if a in m["forbidden_actions"]:
        return False, f"forbidden:{a}"
    if a in m["allowed_actions"] or a == "pass_through_pipeline":
        return True, "ok"
    return False, f"not_in_allowed_list:{a}"
