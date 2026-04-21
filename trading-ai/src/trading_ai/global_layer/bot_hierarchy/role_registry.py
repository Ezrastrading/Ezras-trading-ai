"""
Generic role registry for avenue/gate bot assignment.

Edge logic remains gate/strategy specific; roles here only define governance/intelligence surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class BotRoleSpec:
    role: str
    level: str  # "avenue" | "gate"
    default_active: bool = True
    evidence_contract: Dict[str, object] | None = None
    truth_source: str = ""


DEFAULT_GATE_ROLES: Sequence[BotRoleSpec] = (
    BotRoleSpec(role="scanner_bot", level="gate", truth_source="scanner_framework"),
    BotRoleSpec(role="strategy_bot", level="gate", truth_source="edge_registry"),
    BotRoleSpec(role="risk_bot", level="gate", truth_source="risk_truth"),
    BotRoleSpec(role="execution_validation_bot", level="gate", truth_source="execution_proof"),
    BotRoleSpec(role="exit_manager_bot", level="gate", truth_source="exit_truth"),
    BotRoleSpec(role="rebuy_manager_bot", level="gate", truth_source="rebuy_truth"),
    BotRoleSpec(role="rebuy_decision_bot", level="gate", truth_source="rebuy_truth"),
    BotRoleSpec(role="profit_progression_bot", level="gate", truth_source="profit_truth"),
    BotRoleSpec(role="goal_progression_bot", level="gate", truth_source="progression_truth"),
)

DEFAULT_AVENUE_ROLES: Sequence[BotRoleSpec] = (
    BotRoleSpec(role="avenue_master", level="avenue", truth_source="avenue_registry"),
    BotRoleSpec(role="research_bot", level="avenue", truth_source="edge_research"),
    BotRoleSpec(role="opportunity_ranking_bot", level="avenue", truth_source="opportunity_ranking"),
    BotRoleSpec(role="capital_allocation_bot", level="avenue", truth_source="capital_ledger"),
    BotRoleSpec(role="alerting_bot", level="avenue", truth_source="alerts"),
    BotRoleSpec(role="review_bot", level="avenue", truth_source="ceo_review"),
)


def iter_default_gate_roles(extra: Optional[Iterable[BotRoleSpec]] = None) -> List[BotRoleSpec]:
    out = list(DEFAULT_GATE_ROLES)
    if extra:
        out.extend(list(extra))
    return out


def iter_default_avenue_roles(extra: Optional[Iterable[BotRoleSpec]] = None) -> List[BotRoleSpec]:
    out = list(DEFAULT_AVENUE_ROLES)
    if extra:
        out.extend(list(extra))
    return out

