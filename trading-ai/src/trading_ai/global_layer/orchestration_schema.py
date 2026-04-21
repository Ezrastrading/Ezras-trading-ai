"""Orchestration enums and defaults — permission ladder, bot classes, status."""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Dict, Final, Tuple

from trading_ai.global_layer.system_mission import default_bot_mission_fields


class PermissionLevel(str, Enum):
    """Live order placement is forbidden below execution_authority (enforced in orchestration_permissions)."""

    OBSERVE_ONLY = "observe_only"
    ADVISORY_ONLY = "advisory_only"
    SHADOW_EXECUTION = "shadow_execution"
    CANDIDATE_EXECUTION = "candidate_execution"
    PROMOTED_EXECUTION = "promoted_execution"
    EXECUTION_AUTHORITY = "execution_authority"
    ADMIN_INTERNAL = "admin_internal"


# New bots must never default above advisory + shadow band
DEFAULT_SPAWN_PERMISSION: Final[str] = PermissionLevel.OBSERVE_ONLY.value

# Tunable policy caps (deterministic)
MAX_BOTS_GLOBAL: Final[int] = 64
MAX_BOTS_PER_AVENUE: Final[int] = 12
MAX_BOTS_PER_GATE: Final[int] = 6
MAX_BOTS_PER_CLASS: Final[int] = 8
SPAWN_COOLDOWN_SEC = int(os.environ.get("EZRAS_BOT_SPAWN_COOLDOWN_SEC") or "300")


class OrchestrationBotStatus(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    STALE = "stale"
    PENDING_REVIEW = "pending_review"


class PromotionTier(str, Enum):
    """Staged autonomy — T0 shadow through T5 narrow route-primary (orchestration semantics)."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"


class CapitalAuthorityTier(str, Enum):
    """Capital envelope tier — independent from promotion tier; default C0."""

    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    C5 = "C5"


# Worker / control / execution taxonomy (string ids for registry.bot_class)
BOT_CLASS_SYSTEM_CEO: Final[str] = "system_ceo_bot"
BOT_CLASS_AVENUE_SUP: Final[str] = "avenue_supervisor_bot"
BOT_CLASS_GATE_SUP: Final[str] = "gate_supervisor_bot"
BOT_CLASS_SPAWN_MGR: Final[str] = "bot_spawn_manager"
BOT_CLASS_RISK_GOV: Final[str] = "bot_risk_governor"
BOT_CLASS_COST_GOV: Final[str] = "bot_cost_governor"
BOT_CLASS_TRUTH_AUD: Final[str] = "bot_truth_auditor"

BOT_CLASS_SCANNER: Final[str] = "scanner_bot"
BOT_CLASS_SHADOW_EX: Final[str] = "shadow_execution_bot"
BOT_CLASS_CANDIDATE_EX: Final[str] = "candidate_execution_bot"
BOT_CLASS_PROMOTED_EX: Final[str] = "promoted_execution_bot"

ALL_BOT_CLASSES: Final[Tuple[str, ...]] = (
    BOT_CLASS_SYSTEM_CEO,
    BOT_CLASS_AVENUE_SUP,
    BOT_CLASS_GATE_SUP,
    BOT_CLASS_SPAWN_MGR,
    BOT_CLASS_RISK_GOV,
    BOT_CLASS_COST_GOV,
    BOT_CLASS_TRUTH_AUD,
    BOT_CLASS_SCANNER,
    "route_bot",
    "entry_bot",
    "exit_bot",
    "pnl_bot",
    "slippage_bot",
    "fill_quality_bot",
    "databank_bot",
    "supabase_sync_bot",
    "risk_review_bot",
    "anomaly_bot",
    "learning_bot",
    "research_bot",
    "replay_bot",
    "score_bot",
    "review_bot",
    "report_bot",
    BOT_CLASS_SHADOW_EX,
    BOT_CLASS_CANDIDATE_EX,
    BOT_CLASS_PROMOTED_EX,
)


def default_bot_class_from_legacy_role(role: str) -> str:
    m = {
        "SCANNER": BOT_CLASS_SCANNER,
        "DECISION": "route_bot",
        "EXECUTION": BOT_CLASS_SHADOW_EX,
        "RISK": "risk_review_bot",
        "LEARNING": "learning_bot",
    }
    return m.get(str(role).strip(), BOT_CLASS_SCANNER)


def permission_allows_live_orders(level: str) -> bool:
    return str(level).strip() == PermissionLevel.EXECUTION_AUTHORITY.value


def permission_allows_shadow_simulation(level: str) -> bool:
    return str(level).strip() in (
        PermissionLevel.SHADOW_EXECUTION.value,
        PermissionLevel.CANDIDATE_EXECUTION.value,
        PermissionLevel.PROMOTED_EXECUTION.value,
        PermissionLevel.EXECUTION_AUTHORITY.value,
    )


def compute_assigned_scope(avenue: str, gate: str, route: str) -> str:
    return f"{avenue}|{gate}|{route}"


def compute_duplicate_guard_key(avenue: str, gate: str, route: str, bot_class: str, assigned_scope: str) -> str:
    return f"{avenue}|{gate}|{route}|{bot_class}|{assigned_scope}"


def compute_canonical_owner_key(avenue: str, gate: str, route: str, bot_id: str) -> str:
    return f"{avenue}|{gate}|{route}|{bot_id}"


def promotion_tier_index(tier: str) -> int:
    t = str(tier or "").strip().upper()
    for e in PromotionTier:
        if e.value == t:
            return int(t[1:])
    return 0


def capital_tier_index(tier: str) -> int:
    t = str(tier or "").strip().upper()
    for e in CapitalAuthorityTier:
        if e.value == t:
            return int(t[1:])
    return 0


def permission_and_capabilities_for_promotion_tier(tier: str) -> Tuple[str, Dict[str, bool]]:
    """
    Map staged promotion tier to permission_level + capability flags.
    Live venue orders still require EXECUTION_AUTHORITY + execution_authority.json slot (separate).
    """
    idx = promotion_tier_index(tier)
    caps: Dict[str, bool] = {
        "advisory_influence": idx >= 1,
        "ranking_influence": idx >= 2,
        "sizing_suggestion_influence": idx >= 3,
        "limited_live_lane_support": idx >= 4,
        "route_primary_candidate": idx >= 5,
    }
    if idx <= 0:
        return PermissionLevel.SHADOW_EXECUTION.value, caps
    if idx == 1:
        return PermissionLevel.ADVISORY_ONLY.value, caps
    if idx in (2, 3):
        pl = PermissionLevel.ADVISORY_ONLY.value if idx == 2 else PermissionLevel.CANDIDATE_EXECUTION.value
        return pl, caps
    if idx == 4:
        return PermissionLevel.PROMOTED_EXECUTION.value, caps
    return PermissionLevel.EXECUTION_AUTHORITY.value, caps


def default_bot_record_skeleton(bot_id: str, avenue: str, gate: str, role: str, version: str) -> Dict[str, Any]:
    route = "default"
    bc = default_bot_class_from_legacy_role(role)
    scope = compute_assigned_scope(avenue, gate, route)
    dup_key = compute_duplicate_guard_key(avenue, gate, route, bc, scope)
    owner_key = compute_canonical_owner_key(avenue, gate, route, bot_id)
    _perm, _caps = permission_and_capabilities_for_promotion_tier(PromotionTier.T0.value)
    return {
        "bot_id": bot_id,
        "bot_class": bc,
        "avenue": avenue,
        "gate": gate,
        "route": route,
        "task_family": "general",
        "assigned_scope": scope,
        "status": OrchestrationBotStatus.ACTIVE.value,
        "orchestration_lifecycle": "shadow",
        "lifecycle_state": "shadow",
        "execution_rung": "shadow",
        "permission_level": _perm,
        "promotion_capabilities": _caps,
        "promotion_tier": PromotionTier.T0.value,
        "promotion_target_tier": PromotionTier.T1.value,
        "last_auto_promotion_at": None,
        "last_capital_change_at": None,
        "capital_authority_tier": CapitalAuthorityTier.C0.value,
        "capital_mode": "none",
        "capital_scale_down_state": "ok",
        "scale_up_eligibility": False,
        "emergency_cap_lock": False,
        "authority_change_reason": None,
        "authority_source": "registry_default",
        "spawn_reason": "registry_import_or_legacy",
        "spawn_source_bot_id": "system",
        "created_at": None,
        "updated_at": None,
        "last_heartbeat_at": None,
        "last_review_at": None,
        "current_objective": "",
        "current_constraints": [],
        "token_budget_daily": 40_000,
        "token_budget_remaining": 40_000,
        "confidence_score": 0.5,
        "reliability_score": 0.5,
        "promotion_eligibility": False,
        "demotion_risk": False,
        "disable_reason": None,
        "canonical_owner_key": owner_key,
        "duplicate_guard_key": dup_key,
        "role": role,
        "version": version,
        "performance": {},
        "promotion_scorecard": {
            "shadow_trade_count": 0,
            "evaluation_count": 0,
            "sample_diversity_score": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_slippage_bps": 0.0,
            "avg_latency_ms": 0.0,
            "truth_conflict_unresolved": 0,
            "duplicate_task_violations": 0,
            "unauthorized_writes": 0,
            "promotion_readiness_score": 0.0,
            "loss_streak": 0,
            "clean_live_cycles": 0,
        },
        "governance_flags": {
            "ceo_review_pass": False,
            "risk_review_pass": False,
        },
        "external_eval_signals": {
            "performance_evaluator_ok": False,
            "risk_engine_ok": False,
            "truth_layer_ok": False,
            "orchestration_policy_ok": False,
        },
        "ambition_scorecard": {
            "hit_rate": 0.0,
            "false_positive_rate": 0.0,
            "latency_ms_p50": 0.0,
            "calibration_score": 0.0,
            "budget_efficiency": 0.0,
            "contradiction_count": 0,
        },
        **default_bot_mission_fields(),
        "profitability_score": 0.0,
        "upside_speed_score": 0.0,
        "convergence_score": 0.0,
        "scale_score": 0.0,
        "truth_score": 0.0,
        "capital_efficiency_score": 0.0,
        "token_efficiency_score": 0.0,
        "implementation_speed_score": 0.0,
        "promotion_velocity_score": 0.0,
        "research_usefulness_score": 0.0,
        "progression_score": 0.0,
        "evidence_streak_score": 0.0,
    }
