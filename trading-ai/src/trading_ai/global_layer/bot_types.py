"""Strict bot role and lifecycle typing — one role per bot, no ambiguous overlap."""

from __future__ import annotations

from enum import Enum
from typing import NewType

# Avenue / gate identifiers must remain open-ended to support future avenues/gates.
# Use lightweight type aliases instead of Literal unions to avoid hardcoding.
AvenueId = NewType("AvenueId", str)
GateId = NewType("GateId", str)


class BotRole(str, Enum):
    """Each bot has exactly one role."""

    SCANNER = "SCANNER"
    DECISION = "DECISION"
    EXECUTION = "EXECUTION"
    RISK = "RISK"
    LEARNING = "LEARNING"


class BotLifecycleState(str, Enum):
    """Governed progression; live authority requires promotion + validation pipeline (see bot_lifecycle)."""

    PROPOSED = "proposed"
    INITIALIZED = "initialized"
    SHADOW = "shadow"
    ELIGIBLE = "eligible"
    PROBATION = "probation"
    ACTIVE = "active"
    PROMOTED = "promoted"
    PAUSED = "paused"
    FROZEN = "frozen"
    DEMOTED = "demoted"
    DEGRADED = "degraded"
    RETIRED = "retired"
    ARCHIVED = "archived"


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ExperimentState(str, Enum):
    PROPOSED = "proposed"
    RUNNING = "running"
    PAUSED = "paused"
    EVALUATED = "evaluated"
    ADOPTED = "adopted"
    REJECTED = "rejected"
