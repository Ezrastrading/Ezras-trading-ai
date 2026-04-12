from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class CandidateMarket(BaseModel):
    market_id: str
    slug: Optional[str] = None
    question: str
    volume_usd: Optional[float] = None
    end_date_iso: Optional[str] = None
    days_to_expiry: Optional[float] = None
    implied_probability: Optional[float] = Field(
        default=None,
        description="Primary YES/outcome probability if derivable (0–1)",
    )
    outcome_prices: Optional[List[float]] = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SourceRef(BaseModel):
    url: str
    title: Optional[str] = None
    fetched_at: datetime
    provider: str


class EnrichmentBundle(BaseModel):
    market_id: str
    query: str
    tavily_results: List[SourceRef] = Field(default_factory=list)
    firecrawl_results: List[SourceRef] = Field(default_factory=list)
    gpt_researcher_notes: Optional[str] = None
    gpt_researcher_sources: List[SourceRef] = Field(default_factory=list)


class TradeBrief(BaseModel):
    market_id: str
    market_question: str
    implied_probability: Optional[float] = None
    supporting_evidence: List[str] = Field(default_factory=list)
    opposing_evidence: List[str] = Field(default_factory=list)
    probability_drivers: List[str] = Field(
        default_factory=list,
        description="What would move implied probability up or down",
    )
    uncertainty: str
    edge_hypothesis: str
    signal_score: int = Field(ge=1, le=10)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    model: Optional[str] = None


class AlertRecord(BaseModel):
    id: Optional[int] = None
    market_id: str
    brief_created_at: datetime
    channel: str
    payload_summary: str
    sent_at: datetime


class DecisionRecord(BaseModel):
    id: Optional[int] = None
    market_id: str
    brief_created_at: datetime
    action: str
    notes: Optional[str] = None
    decided_at: datetime = Field(default_factory=datetime.utcnow)
