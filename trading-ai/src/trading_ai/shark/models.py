"""Core dataclasses for Shark — markets, hunts, scores, execution intents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class HuntType(str, Enum):
    DEAD_MARKET_CONVERGENCE = "dead_market_convergence"
    STRUCTURAL_ARBITRAGE = "structural_arbitrage"
    CROSS_PLATFORM_MISPRICING = "cross_platform_mispricing"
    STATISTICAL_WINDOW = "statistical_window"
    LIQUIDITY_IMBALANCE_FADE = "liquidity_imbalance_fade"
    NEAR_ZERO_ACCUMULATION = "near_zero_accumulation"
    OPTIONS_BINARY = "options_binary"
    CRYPTO_SCALP = "crypto_scalp"
    PURE_ARBITRAGE = "pure_arbitrage"
    NEAR_RESOLUTION = "near_resolution"
    ORDER_BOOK_IMBALANCE = "order_book_imbalance"
    VOLUME_SPIKE = "volume_spike"


class OpportunityTier(str, Enum):
    TIER_A = "TIER_A"
    TIER_B = "TIER_B"
    TIER_C = "TIER_C"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"


class CapitalPhase(str, Enum):
    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"
    PHASE_3 = "phase_3"
    PHASE_4 = "phase_4"
    PHASE_5 = "phase_5"


@dataclass
class MarketSnapshot:
    market_id: str
    outlet: str
    yes_price: float
    no_price: float
    volume_24h: float
    time_to_resolution_seconds: float
    resolution_criteria: str
    last_price_update_timestamp: float  # unix
    underlying_data_if_available: Optional[Dict[str, Any]] = None
    canonical_event_key: Optional[str] = None
    # Extended fields for hunt types 4–5
    market_type_key: Optional[str] = None
    historical_yes_rate: Optional[float] = None
    historical_sample_count: int = 0
    scheduled_event_in_seconds: Optional[float] = None
    order_book_bid_depth_yes: float = 0.0
    order_book_bid_depth_no: float = 0.0
    imbalance_since_unix: Optional[float] = None
    required_position_dollars: float = 100.0
    market_category: str = "default"
    # Polymarket short-horizon / microstructure (optional)
    question_text: Optional[str] = None
    end_timestamp_unix: Optional[float] = None
    end_date_seconds: Optional[float] = None  # alias epoch for resolution (optional; same role as end_timestamp_unix)
    best_ask_yes: Optional[float] = None
    best_ask_no: Optional[float] = None
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None


@dataclass
class HuntSignal:
    hunt_type: HuntType
    edge_after_fees: float
    confidence: float  # 0..1
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoredOpportunity:
    market: MarketSnapshot
    hunts: List[HuntSignal]
    edge_size: float
    confidence: float
    liquidity_score: float
    resolution_speed_score: float
    strategy_performance_weight: float
    score: float
    tier: OpportunityTier
    tier_sizing_multiplier: float


@dataclass
class ExecutionIntent:
    market_id: str
    outlet: str
    side: str  # "yes" | "no"
    stake_fraction_of_capital: float
    edge_after_fees: float
    estimated_win_probability: float
    hunt_types: List[HuntType]
    source: str
    gap_exploit: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)
    expected_price: float = 0.0
    notional_usd: float = 0.0
    shares: int = 0
    is_mana: bool = False


@dataclass
class OrderResult:
    order_id: str
    filled_price: float
    filled_size: float
    timestamp: float
    status: str
    outlet: str
    raw: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    reason: Optional[str] = None


@dataclass
class ConfirmationResult:
    actual_fill_price: float
    actual_fill_size: float
    slippage_pct: float
    confirmed: bool
    high_slippage_warning: bool = False
    unfilled_cancelled: bool = False


@dataclass
class OpenPosition:
    position_id: str
    outlet: str
    market_id: str
    side: str
    entry_price: float
    shares: float
    notional_usd: float
    order_id: str
    opened_at: float
    strategy_key: str = "shark_default"
    hunt_types: List[str] = field(default_factory=list)
    market_category: str = "default"
    expected_edge: float = 0.0
    condition_id: Optional[str] = None
    token_id: Optional[str] = None
    margin_borrowed_usd: float = 0.0
    claude_reasoning: Optional[str] = None
    claude_confidence: Optional[float] = None
    claude_true_probability: Optional[float] = None
    claude_decision: Optional[str] = None


@dataclass
class GapObservation:
    """Single measurement for a structural gap hypothesis."""

    gap_type: str
    lag_seconds: float
    consistency_hint: float
    volume_available: float
    edge_per_trade: float
    competition: str  # none | some | heavy


@dataclass
class StructuralGapPattern:
    gap_type: str
    observations: List[GapObservation]
    gap_score: float
    confirmed: bool
    meta: Dict[str, Any] = field(default_factory=dict)
