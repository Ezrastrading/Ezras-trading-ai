from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class GateFamily(str, Enum):
    CORE = "core"
    ASYMMETRIC = "asymmetric"


class TradeType(str, Enum):
    CORE = "core"
    ASYMMETRIC = "asymmetric"


class AsymmetricThesisType(str, Enum):
    PENNY_ASYMMETRY = "PENNY_ASYMMETRY"
    LONGSHOT_BIAS = "LONGSHOT_BIAS"
    EVENT_CONVEXITY = "EVENT_CONVEXITY"
    CHEAP_OPTIONALITY = "CHEAP_OPTIONALITY"
    STRUCTURAL_MISPRICING = "STRUCTURAL_MISPRICING"
    FORCED_LIQUIDITY_DISCOUNT = "FORCED_LIQUIDITY_DISCOUNT"
    UNDERPRICED_TAIL = "UNDERPRICED_TAIL"
    HIGH_SKEW_PAYOUT = "HIGH_SKEW_PAYOUT"
    LOTTERY_EV = "LOTTERY_EV"
    CATALYST_OPTIONALITY = "CATALYST_OPTIONALITY"


class AsymmetricExitMode(str, Enum):
    HOLD_TO_RESOLUTION = "HOLD_TO_RESOLUTION"
    TRADE_THE_BOUNCE = "TRADE_THE_BOUNCE"
    PARTIAL_AT_MULTIPLES = "PARTIAL_AT_MULTIPLES"
    LADDERED_EXIT = "LADDERED_EXIT"
    TIME_OR_THESIS_INVALIDATION_EXIT = "TIME_OR_THESIS_INVALIDATION_EXIT"


class ConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class AsymmetricEVScenario:
    scenario_id: str
    probability: float
    payout_usd: float
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AsymmetricEVResult:
    truth_version: str
    expected_value_gross_usd: float
    expected_value_net_usd: float
    expected_multiple: float
    ev_per_dollar: float
    payoff_skew_ratio: float
    tail_dependency_score: float
    confidence_band: str
    scenario_entropy: float
    model_quality_score: float
    scenario_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    costs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AsymmetricBatchRecord:
    truth_version: str
    batch_id: str
    avenue: str
    gate_id: str
    gate_family: str
    start_time_utc: str
    deployed_capital_usd: float
    number_of_positions: int
    avg_ev_net_usd: float
    median_ev_net_usd: float
    expected_hit_rate: Optional[float]
    actual_hit_rate: Optional[float]
    biggest_winner_usd: float
    total_realized_pnl_usd: float
    unresolved_positions_count: int
    expired_zero_count: int
    batch_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AsymmetricTradeRecord:
    trade_id: str
    gate_family: str
    gate_id: str
    trade_type: str
    avenue: str
    instrument: str
    market_type: str
    asym_style: str
    asym_thesis_type: str
    entry_timestamp_utc: str
    entry_price: float
    quantity: float
    max_loss_usd: float
    expected_value_net_usd: float
    expected_multiple: float
    long_tail_rank: float
    batch_id: str
    batch_position_index: int
    portfolio_role: str
    payout_profile_json: Dict[str, Any] = field(default_factory=dict)
    estimated_probabilities: Dict[str, Any] = field(default_factory=dict)
    estimated_payouts: Dict[str, Any] = field(default_factory=dict)
    resolution_or_event_window: Dict[str, Any] = field(default_factory=dict)
    exit_mode: str = AsymmetricExitMode.HOLD_TO_RESOLUTION.value
    exit_trigger_levels: Dict[str, Any] = field(default_factory=dict)
    thesis_invalidators: List[str] = field(default_factory=list)
    max_hold_window: Optional[Dict[str, Any]] = None
    should_trade: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def validate_asymmetric_trade_record(tr: Mapping[str, Any], *, allow_probe_without_batch: bool = False) -> List[str]:
    errs: List[str] = []
    if str(tr.get("gate_family") or "").strip() != GateFamily.ASYMMETRIC.value:
        errs.append("gate_family_must_be_asymmetric")
    if str(tr.get("trade_type") or "").strip() != TradeType.ASYMMETRIC.value:
        errs.append("trade_type_must_be_asymmetric")
    if not str(tr.get("gate_id") or "").strip():
        errs.append("missing_gate_id")
    if not str(tr.get("avenue") or "").strip():
        errs.append("missing_avenue")
    if not str(tr.get("instrument") or "").strip():
        errs.append("missing_instrument")
    if not str(tr.get("market_type") or "").strip():
        errs.append("missing_market_type")
    if not str(tr.get("asym_thesis_type") or "").strip():
        errs.append("missing_asym_thesis_type")
    else:
        try:
            _ = AsymmetricThesisType(str(tr.get("asym_thesis_type")))
        except Exception:
            errs.append("invalid_asym_thesis_type")
    ev = tr.get("expected_value_net_usd")
    if ev is None:
        errs.append("missing_expected_value_net_usd")
    else:
        if _f(ev, -1e9) != _f(ev, -1e9):
            errs.append("expected_value_net_usd_nan")
    if not allow_probe_without_batch:
        if not str(tr.get("batch_id") or "").strip():
            errs.append("missing_batch_id")
    if "batch_position_index" not in tr:
        errs.append("missing_batch_position_index")
    if not str(tr.get("portfolio_role") or "").strip():
        errs.append("missing_portfolio_role")
    return errs


def canonical_gate_id(*, avenue: str, gate_family: str) -> str:
    a = str(avenue or "").strip().upper()
    gf = str(gate_family or "").strip().lower()
    if gf == GateFamily.CORE.value:
        return f"{a}_CORE"
    if gf == GateFamily.ASYMMETRIC.value:
        return f"{a}_ASYM"
    return f"{a}_UNKNOWN"


def encode_payout_profile_json(profile: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(profile), ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"

