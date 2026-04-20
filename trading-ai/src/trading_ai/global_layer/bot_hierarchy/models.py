"""Canonical hierarchy bot and gate-candidate models — conservative enums, fail-closed."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# --- Enums (exact, conservative) ---


class HierarchyBotType(str, Enum):
    EZRA_GOVERNOR = "ezra_governor"
    AVENUE_MASTER = "avenue_master"
    GATE_MANAGER = "gate_manager"
    GATE_WORKER = "gate_worker"


class HierarchyAuthorityLevel(str, Enum):
    """Intelligence / governance tier — not venue execution authority."""

    GOVERNANCE_ROOT = "governance_root"
    AVENUE_INTELLIGENCE = "avenue_intelligence"
    GATE_INTELLIGENCE = "gate_intelligence"
    WORKER_NARROW = "worker_narrow"


class HierarchyBotStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    DEGRADED = "degraded"
    PAUSED = "paused"
    RETIRED = "retired"


class HierarchyLifecycleStage(str, Enum):
    """Operational lifecycle for hierarchy bots (not gate research ladder)."""

    INIT = "init"
    OBSERVING = "observing"
    SUPPORTING = "supporting"
    RETIRED = "retired"


class GateCandidateStage(str, Enum):
    """Research → promotion ladder — single forward path; no skips (enforced in gate_discovery)."""

    DISCOVERED = "discovered"
    DOCUMENTED = "documented"
    HYPOTHESIS_DEFINED = "hypothesis_defined"
    REPLAY_READY = "replay_ready"
    REPLAY_TESTED = "replay_tested"
    SIM_CANDIDATE = "sim_candidate"
    SIM_PASSED = "sim_passed"
    STAGED_RUNTIME_CANDIDATE = "staged_runtime_candidate"
    STAGED_RUNTIME_PASSED = "staged_runtime_passed"
    SUPERVISED_LIVE_CANDIDATE = "supervised_live_candidate"
    SUPERVISED_LIVE_PASSED = "supervised_live_passed"
    AUTONOMOUS_CANDIDATE = "autonomous_candidate"
    AUTONOMOUS_APPROVED = "autonomous_approved"
    AUTONOMOUS_LIVE_ENABLED = "autonomous_live_enabled"


GATE_CANDIDATE_STAGE_ORDER: tuple[str, ...] = tuple(s.value for s in GateCandidateStage)


class LivePermissions(BaseModel):
    """Explicit live capability flags — hierarchy bots default all false."""

    venue_orders: bool = False
    runtime_switch: bool = False
    capital_allocation_mutate: bool = False


class HierarchyBotRecord(BaseModel):
    bot_id: str = Field(..., min_length=1)
    bot_name: str = Field(..., min_length=1)
    bot_type: HierarchyBotType
    avenue_id: str = Field(..., min_length=1)
    gate_id: Optional[str] = None
    parent_bot_id: Optional[str] = None
    authority_level: HierarchyAuthorityLevel
    live_permissions: LivePermissions = Field(default_factory=LivePermissions)
    research_permissions: bool = True
    can_propose_new_gate: bool = False
    can_spawn_child_bots: bool = False
    can_modify_live_logic: bool = False
    status: HierarchyBotStatus = HierarchyBotStatus.PLANNED
    lifecycle_stage: HierarchyLifecycleStage = HierarchyLifecycleStage.INIT
    knowledge_scope: List[str] = Field(default_factory=list)
    execution_scope: List[str] = Field(default_factory=list)
    reporting_scope: List[str] = Field(default_factory=list)
    current_objectives: List[str] = Field(default_factory=list)
    success_metrics: Dict[str, Any] = Field(default_factory=dict)
    failure_conditions: List[str] = Field(default_factory=list)
    safety_constraints: List[str] = Field(default_factory=list)
    linked_orchestration_bot_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    @field_validator("gate_id", mode="before")
    @classmethod
    def empty_gate_to_none(cls, v: Any) -> Any:
        if v is None:
            return None
        s = str(v).strip()
        return None if not s or s.lower() == "none" else s

    @model_validator(mode="after")
    def enforce_hierarchy_authority_rules(self) -> HierarchyBotRecord:
        lp = self.live_permissions
        if lp.venue_orders or lp.runtime_switch or lp.capital_allocation_mutate:
            raise ValueError("hierarchy_bot_forbids_non_false_live_permissions")
        if self.can_modify_live_logic:
            raise ValueError("hierarchy_bot_cannot_modify_live_logic")
        if self.bot_type == HierarchyBotType.EZRA_GOVERNOR:
            if self.parent_bot_id:
                raise ValueError("ezra_governor_must_have_null_parent")
            if self.authority_level != HierarchyAuthorityLevel.GOVERNANCE_ROOT:
                raise ValueError("ezra_must_use_governance_root_authority")
        if self.bot_type == HierarchyBotType.AVENUE_MASTER:
            if not self.parent_bot_id:
                raise ValueError("avenue_master_requires_parent_ezra")
            if self.gate_id is not None:
                raise ValueError("avenue_master_gate_id_must_be_null")
        if self.bot_type == HierarchyBotType.GATE_MANAGER:
            if not self.parent_bot_id or not self.gate_id:
                raise ValueError("gate_manager_requires_parent_and_gate_id")
        if self.bot_type == HierarchyBotType.GATE_WORKER:
            if not self.parent_bot_id or not self.gate_id:
                raise ValueError("gate_worker_requires_parent_and_gate_id")
        return self


class GateCandidateRecord(BaseModel):
    candidate_id: str = Field(..., min_length=4)
    avenue_id: str = Field(..., min_length=1)
    gate_id: str = Field(..., min_length=1)
    strategy_thesis: str = ""
    edge_hypothesis: str = ""
    execution_path: str = ""
    expected_conditions: List[str] = Field(default_factory=list)
    expected_pnl_shape_notes: str = Field(
        default="",
        description="Descriptive only — not a performance guarantee.",
    )
    limits: Dict[str, Any] = Field(default_factory=dict)
    constraints: List[str] = Field(default_factory=list)
    kill_conditions: List[str] = Field(default_factory=list)
    required_proofs: List[str] = Field(default_factory=list)
    stage: GateCandidateStage = GateCandidateStage.DISCOVERED
    gate_manager_bot_id: Optional[str] = None
    recommended_worker_roles: List[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    blocked_reasons: List[str] = Field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_hierarchy_bot(
    *,
    bot_id: str,
    bot_name: str,
    bot_type: HierarchyBotType,
    avenue_id: str,
    authority_level: HierarchyAuthorityLevel,
    parent_bot_id: Optional[str],
    gate_id: Optional[str],
    **extra: Any,
) -> HierarchyBotRecord:
    now = utc_now_iso()
    base: Dict[str, Any] = {
        "bot_id": bot_id,
        "bot_name": bot_name,
        "bot_type": bot_type,
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "parent_bot_id": parent_bot_id,
        "authority_level": authority_level,
        "live_permissions": LivePermissions(),
        "research_permissions": True,
        "can_propose_new_gate": bot_type == HierarchyBotType.AVENUE_MASTER,
        "can_spawn_child_bots": bot_type in (HierarchyBotType.EZRA_GOVERNOR, HierarchyBotType.AVENUE_MASTER, HierarchyBotType.GATE_MANAGER),
        "can_modify_live_logic": False,
        "created_at": now,
        "updated_at": now,
    }
    base.update(extra)
    return HierarchyBotRecord.model_validate(base)
