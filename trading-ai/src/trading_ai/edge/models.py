"""Edge domain types — status lifecycle and classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class EdgeStatus(str, Enum):
    CANDIDATE = "candidate"
    TESTING = "testing"
    VALIDATED = "validated"
    SCALED = "scaled"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class EdgeType(str, Enum):
    MOMENTUM = "momentum"
    SPREAD = "spread"
    PROBABILITY = "probability"
    VOLATILITY = "volatility"
    LATENCY = "latency"
    UNKNOWN = "unknown"


def classify_edge_type(hypothesis: str) -> str:
    h = (hypothesis or "").lower()
    if any(x in h for x in ("latency", "delay", "ws ", "websocket")):
        return EdgeType.LATENCY.value
    if any(x in h for x in ("spread", "arb", "cross", "basis")):
        return EdgeType.SPREAD.value
    if any(x in h for x in ("vol", "volatility", "iv ", "regime")):
        return EdgeType.VOLATILITY.value
    if any(x in h for x in ("odds", "probability", "prediction", "market maker", "skew")):
        return EdgeType.PROBABILITY.value
    if any(x in h for x in ("momentum", "trend", "continuation", "breakout")):
        return EdgeType.MOMENTUM.value
    return EdgeType.UNKNOWN.value


@dataclass
class EdgeRecord:
    edge_id: str
    avenue: str
    edge_type: str
    hypothesis_text: str
    required_conditions: Dict[str, Any]
    status: str
    confidence: float = 0.25
    linked_strategy_id: Optional[str] = None
    source_research_ts: Optional[str] = None
    source: str = "research"
    created_at: str = ""
    updated_at: str = ""
    notes: str = ""
    rejection_reason: Optional[str] = None
    promotion_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _utc_now()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_json(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "avenue": self.avenue,
            "edge_type": self.edge_type,
            "hypothesis_text": self.hypothesis_text,
            "required_conditions": dict(self.required_conditions),
            "status": self.status,
            "confidence": float(self.confidence),
            "linked_strategy_id": self.linked_strategy_id,
            "source_research_ts": self.source_research_ts,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
            "rejection_reason": self.rejection_reason,
            "promotion_history": list(self.promotion_history),
        }

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "EdgeRecord":
        return EdgeRecord(
            edge_id=str(d["edge_id"]),
            avenue=str(d.get("avenue") or "unknown"),
            edge_type=str(d.get("edge_type") or EdgeType.UNKNOWN.value),
            hypothesis_text=str(d.get("hypothesis_text") or ""),
            required_conditions=dict(d.get("required_conditions") or {}),
            status=str(d.get("status") or EdgeStatus.CANDIDATE.value),
            confidence=float(d.get("confidence") or 0.25),
            linked_strategy_id=d.get("linked_strategy_id"),
            source_research_ts=d.get("source_research_ts"),
            source=str(d.get("source") or "research"),
            created_at=str(d.get("created_at") or ""),
            updated_at=str(d.get("updated_at") or ""),
            notes=str(d.get("notes") or ""),
            rejection_reason=d.get("rejection_reason"),
            promotion_history=list(d.get("promotion_history") or []),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EdgeTradeMetrics:
    edge_id: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    post_fee_expectancy: float
    net_pnl: float
    pnl_per_trade: float
    gross_fees: float
    max_drawdown: float
    variance_pnl: float
    stability_score: float
    sample_net_pnls: List[float] = field(default_factory=list)
