"""Structured internal ticket models — append-only store, explicit fields."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TicketStatus(str, Enum):
    open = "open"
    investigating = "investigating"
    routed = "routed"
    resolved = "resolved"
    archived = "archived"


class TicketSeverity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class TicketType(str, Enum):
    execution_incident = "execution_incident"
    market_structure_incident = "market_structure_incident"
    strategy_degradation = "strategy_degradation"
    edge_decay = "edge_decay"
    edge_opportunity = "edge_opportunity"
    liquidity_warning = "liquidity_warning"
    slippage_warning = "slippage_warning"
    partial_fill_incident = "partial_fill_incident"
    timeout_incident = "timeout_incident"
    quote_policy_mismatch = "quote_policy_mismatch"
    runtime_policy_mismatch = "runtime_policy_mismatch"
    venue_behavior_change = "venue_behavior_change"
    data_quality_incident = "data_quality_incident"
    reconciliation_incident = "reconciliation_incident"
    ratio_problem = "ratio_problem"
    reserve_problem = "reserve_problem"
    operator_visibility_problem = "operator_visibility_problem"
    false_positive_signal = "false_positive_signal"
    false_negative_signal = "false_negative_signal"
    scanner_gap = "scanner_gap"
    regime_shift = "regime_shift"
    pnl_anomaly = "pnl_anomaly"
    learning_update_needed = "learning_update_needed"
    market_research_needed = "market_research_needed"
    instrument_research_needed = "instrument_research_needed"
    gate_design_review_needed = "gate_design_review_needed"
    avenue_design_review_needed = "avenue_design_review_needed"


def new_ticket_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"tk_{ts}_{uuid.uuid4().hex[:8]}"


class Ticket(BaseModel):
    ticket_id: str = Field(default_factory=new_ticket_id)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: TicketStatus = TicketStatus.open
    severity: TicketSeverity = TicketSeverity.info
    category: str = ""
    ticket_type: TicketType = TicketType.execution_incident

    avenue_id: str = ""
    gate_id: str = ""
    venue: str = ""
    market_type: str = ""
    instrument_type: str = ""
    product_id: str = ""
    contract_id: str = ""
    market_id: str = ""

    source_component: str = ""
    trigger_event: str = ""
    human_plain_english_summary: str = ""
    machine_summary: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    likely_root_cause: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    recommended_action: str = ""
    auto_action_taken: str = ""

    ceo_review_required: bool = False
    learning_update_required: bool = False
    safe_to_auto_close: bool = False

    routed_domains: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    def to_json_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class RouteResult(BaseModel):
    ticket_id: str
    domains: List[str]
    routing_reason: str = ""
    routed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    model_config = {"extra": "forbid"}


class CEOSessionArtifact(BaseModel):
    ticket_id: str
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    what_happened: str = ""
    why_it_likely_happened: str = ""
    what_system_assumption_broke: str = ""
    what_should_not_be_done_next_time: str = ""
    what_should_be_done_next_time: str = ""
    error_class: Literal[
        "execution",
        "market",
        "policy",
        "strategy",
        "operator",
        "unknown",
    ] = "unknown"
    was_avoidable: Optional[bool] = None
    policy_should_change: bool = False
    strategy_should_change: bool = False
    market_conditions_changed: bool = False
    one_off_or_pattern: Literal["one_off", "pattern", "unclear"] = "unclear"
    alter_gate_a_gate_b_or_avenue_behavior: str = ""
    operator_intervention_needed: bool = False
    ai_can_safely_update_learning_files: bool = False
    confidence: float = 0.0
    ceo_statement_mode: Literal[
        "templated_summary",
        "evidence_backed_summary",
        "venue_verified_summary",
        "hypothesis_note",
        "operator_follow_up_required",
    ] = "templated_summary"
    evidence_chain_present: bool = False
    venue_truth_verified: bool = False
    confidence_basis: str = "ticket_fields_only"
    independent_verification_performed: bool = False

    model_config = {"extra": "forbid"}
