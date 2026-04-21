"""Canonical research record types — structured, scoped, evidence-honest (no fake edge claims)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class ResearchStatus(str, Enum):
    hypothesis = "hypothesis"
    under_research = "under_research"
    mock_supported = "mock_supported"
    staged_supported = "staged_supported"
    live_supported = "live_supported"
    degraded = "degraded"
    archived = "archived"
    rejected = "rejected"


def new_research_record_id(prefix: str = "er") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}_{ts}_{uuid.uuid4().hex[:10]}"


class ResearchRecordCore(BaseModel):
    """Shared fields for every research artifact row — one universal schema."""

    record_id: str = Field(default_factory=new_research_record_id)
    record_kind: str = "research_core"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    avenue_id: str = ""
    gate_id: str = ""
    venue: str = ""
    market_type: str = ""
    instrument_type: str = ""
    product_id: str = ""
    market_id: str = ""
    contract_id: str = ""

    strategy_name: str = ""
    edge_name: str = ""
    latency_profile_name: str = ""

    current_status: ResearchStatus = ResearchStatus.hypothesis
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence_refs: List[str] = Field(default_factory=list)
    supporting_ticket_ids: List[str] = Field(default_factory=list)
    supporting_test_ids: List[str] = Field(default_factory=list)
    supporting_artifact_paths: List[str] = Field(default_factory=list)

    conditions_where_it_works: str = ""
    conditions_where_it_fails: str = ""
    key_risks: str = ""
    edge_mechanism: str = ""
    execution_requirements: str = ""
    liquidity_requirements: str = ""
    latency_requirements: str = ""
    venue_specific_notes: str = ""
    comparison_summary: str = ""
    recommended_next_test: str = ""
    operator_plain_english_summary: str = ""

    extra: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_json_dict(self) -> Dict[str, Any]:
        d = self.model_dump(mode="json")
        d["current_status"] = (
            self.current_status.value if isinstance(self.current_status, ResearchStatus) else self.current_status
        )
        return d


class ResearchDomainRecord(ResearchRecordCore):
    record_kind: Literal["domain"] = "domain"


class StrategyResearchRecord(ResearchRecordCore):
    record_kind: Literal["strategy_research"] = "strategy_research"


class EdgeResearchRecord(ResearchRecordCore):
    record_kind: Literal["edge_research"] = "edge_research"


class LatencyResearchRecord(ResearchRecordCore):
    record_kind: Literal["latency_research"] = "latency_research"


class InstrumentResearchRecord(ResearchRecordCore):
    record_kind: Literal["instrument_research"] = "instrument_research"


class VenueResearchRecord(ResearchRecordCore):
    record_kind: Literal["venue_research"] = "venue_research"


class GateResearchRecord(ResearchRecordCore):
    record_kind: Literal["gate_research"] = "gate_research"


class AvenueResearchRecord(ResearchRecordCore):
    record_kind: Literal["avenue_research"] = "avenue_research"


class ResearchComparisonRecord(BaseModel):
    """Relative comparison — explicit about hypothesis vs staged/live support."""

    record_id: str = Field(default_factory=new_research_record_id)
    record_kind: Literal["comparison"] = "comparison"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    avenue_id: str = ""
    gate_id: str = ""
    dimension: str = ""
    left_record_id: str = ""
    right_record_id: str = ""
    left_label: str = ""
    right_label: str = ""

    why_one_is_better: str = ""
    where_one_is_better: str = ""
    where_one_fails: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence_count: int = 0
    difference_evidence_tier: str = "hypothesis_only"
    venue: str = ""
    market_type: str = ""
    instrument_type: str = ""

    operator_plain_english_summary: str = ""
    supporting_artifact_paths: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    def to_json_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


RecordUnion = Union[
    ResearchDomainRecord,
    StrategyResearchRecord,
    EdgeResearchRecord,
    LatencyResearchRecord,
    InstrumentResearchRecord,
    VenueResearchRecord,
    GateResearchRecord,
    AvenueResearchRecord,
    ResearchRecordCore,
]


def parse_record_dict(d: Dict[str, Any]) -> ResearchRecordCore:
    """Hydrate the correct subclass from a JSON dict."""
    kind = d.get("record_kind") or "research_core"
    mapping = {
        "domain": ResearchDomainRecord,
        "strategy_research": StrategyResearchRecord,
        "edge_research": EdgeResearchRecord,
        "latency_research": LatencyResearchRecord,
        "instrument_research": InstrumentResearchRecord,
        "venue_research": VenueResearchRecord,
        "gate_research": GateResearchRecord,
        "avenue_research": AvenueResearchRecord,
        "research_core": ResearchRecordCore,
    }
    cls = mapping.get(str(kind), ResearchRecordCore)
    return cls.model_validate(d)


def parse_comparison_dict(d: Dict[str, Any]) -> ResearchComparisonRecord:
    return ResearchComparisonRecord.model_validate(d)
