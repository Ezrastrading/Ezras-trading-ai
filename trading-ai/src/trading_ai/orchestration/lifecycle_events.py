"""Canonical trade lifecycle stages and event envelope (avenue-agnostic)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict


class LoopStage(str, Enum):
    SCAN = "scan"
    RANK = "rank"
    SELECT = "select"
    PREFLIGHT = "preflight"
    BUY = "buy"
    CONFIRM_BUY_FILL = "confirm_buy_fill"
    SELL_OR_EXIT = "sell_or_exit"
    CONFIRM_SELL_FILL = "confirm_sell_fill"
    LOG = "log"
    SYNC = "sync"
    EVALUATE = "evaluate"
    REBUY_CANDIDATE = "rebuy_candidate"


class ProofKind(str, Enum):
    CODE_ONLY = "code_only"
    MOCK_PROVEN = "mock_proven"
    STAGED_PROVEN = "staged_proven"
    RUNTIME_VALIDATION = "runtime_validation"
    LIVE_VENUE = "live_venue"


class TradeLifecycleEvent(TypedDict, total=False):
    """Single event in a trade round-trip — adapters map venue payloads into this shape."""

    event_id: str
    stage: str
    avenue_id: str
    avenue_name: str
    trading_gate: str
    strategy_id: str
    edge_lane: str
    product_id: str
    symbol: str
    side: str
    entry_intent_ts: str
    buy_fill_truth: Dict[str, Any]
    sell_fill_truth: Dict[str, Any]
    realized_pnl: Optional[float]
    fees: Optional[float]
    latency_ms: Optional[float]
    adaptive_context: Dict[str, Any]
    governance_context: Dict[str, Any]
    proof_kind: str
    execution_source: str
    validation_source: str
    meta: Dict[str, Any]


def new_event_envelope(*, stage: LoopStage, avenue_id: str, **extra: Any) -> Dict[str, Any]:
    return {
        "stage": stage.value,
        "avenue_id": avenue_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


CANONICAL_LOOP_ORDER: List[str] = [s.value for s in LoopStage]
