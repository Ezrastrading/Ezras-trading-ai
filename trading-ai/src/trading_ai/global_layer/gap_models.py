from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence


class GapType(str, Enum):
    price_lag = "price_lag"
    probability_gap = "probability_gap"
    volatility_gap = "volatility_gap"
    structural_gap = "structural_gap"
    behavioral_gap = "behavioral_gap"


class ExecutionMode(str, Enum):
    maker = "maker"
    taker = "taker"
    hybrid = "hybrid"


REQUIRED_CANDIDATE_FIELDS: Sequence[str] = (
    # Non-negotiable contract
    "edge_percent",
    "edge_score",
    "confidence_score",
    "execution_mode",
    "gap_type",
    # Required by gap engine + sizing
    "estimated_true_value",
    "liquidity_score",
    # Required for strict threshold gating (no “unknown economics”)
    "fees_estimate",
    "slippage_estimate",
)


@dataclass(frozen=True)
class UniversalGapCandidate:
    """
    Universal gap candidate schema.

    HARD RULES:
    - No defaults, no assumptions: every field is required and must be explicitly computed.
    - `must_trade` must be True to permit any live BUY.
    """

    candidate_id: str
    edge_percent: float
    edge_score: float
    confidence_score: float
    execution_mode: str
    gap_type: str
    estimated_true_value: float
    liquidity_score: float
    fees_estimate: float
    slippage_estimate: float
    must_trade: bool
    risk_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "edge_percent": self.edge_percent,
            "edge_score": self.edge_score,
            "confidence_score": self.confidence_score,
            "execution_mode": self.execution_mode,
            "gap_type": self.gap_type,
            "estimated_true_value": self.estimated_true_value,
            "liquidity_score": self.liquidity_score,
            "fees_estimate": self.fees_estimate,
            "slippage_estimate": self.slippage_estimate,
            "must_trade": self.must_trade,
            "risk_flags": list(self.risk_flags or []),
        }


@dataclass(frozen=True)
class CandidateValidationResult:
    ok: bool
    missing_fields: List[str]
    errors: List[str]


_CANDIDATE_CTX: contextvars.ContextVar[Optional[UniversalGapCandidate]] = contextvars.ContextVar(
    "universal_gap_candidate_ctx", default=None
)

_LIVE_BUY_AUTHORITY_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "avenue_a_live_buy_authority_ctx", default=None
)


def candidate_context_get() -> Optional[UniversalGapCandidate]:
    return _CANDIDATE_CTX.get()


def candidate_context_set(c: UniversalGapCandidate) -> contextvars.Token:
    return _CANDIDATE_CTX.set(c)


def candidate_context_reset(tok: contextvars.Token) -> None:
    _CANDIDATE_CTX.reset(tok)


def new_universal_candidate_id(*, prefix: str = "ugc") -> str:
    p = str(prefix or "ugc").strip()
    return f"{p}_{uuid.uuid4().hex[:16]}"


def authoritative_live_buy_path_get() -> Optional[str]:
    return _LIVE_BUY_AUTHORITY_CTX.get()


def authoritative_live_buy_path_set(path_id: str) -> contextvars.Token:
    return _LIVE_BUY_AUTHORITY_CTX.set(str(path_id or "").strip() or None)


def authoritative_live_buy_path_reset(tok: contextvars.Token) -> None:
    _LIVE_BUY_AUTHORITY_CTX.reset(tok)


def validate_candidate_fields(candidate: Any) -> CandidateValidationResult:
    missing: List[str] = []
    errors: List[str] = []

    if candidate is None:
        return CandidateValidationResult(
            ok=False,
            missing_fields=list(REQUIRED_CANDIDATE_FIELDS),
            errors=["candidate_missing"],
        )

    raw: Optional[Mapping[str, Any]]
    if isinstance(candidate, UniversalGapCandidate):
        raw = candidate.to_dict()
    elif isinstance(candidate, Mapping):
        raw = candidate
    else:
        return CandidateValidationResult(
            ok=False,
            missing_fields=list(REQUIRED_CANDIDATE_FIELDS),
            errors=["candidate_type_invalid"],
        )

    def _missing(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, str) and not v.strip():
            return True
        return False

    for k in REQUIRED_CANDIDATE_FIELDS:
        if k not in raw or _missing(raw.get(k)):
            missing.append(k)

    def _num(name: str) -> Optional[float]:
        if name in missing:
            return None
        try:
            v = float(raw.get(name))
        except (TypeError, ValueError):
            errors.append(f"{name}:not_numeric")
            return None
        if v != v:
            errors.append(f"{name}:nan")
            return None
        return v

    edge_percent = _num("edge_percent")
    edge_score = _num("edge_score")
    confidence_score = _num("confidence_score")
    liquidity_score = _num("liquidity_score")
    estimated_true_value = _num("estimated_true_value")
    fees_estimate = _num("fees_estimate")
    slippage_estimate = _num("slippage_estimate")

    if "gap_type" not in missing:
        gt = str(raw.get("gap_type") or "")
        if gt not in {x.value for x in GapType}:
            errors.append("gap_type:unknown")
    if "execution_mode" not in missing:
        em = str(raw.get("execution_mode") or "")
        if em not in {x.value for x in ExecutionMode}:
            errors.append("execution_mode:unknown")

    if estimated_true_value is not None and estimated_true_value <= 0:
        errors.append("estimated_true_value:non_positive")
    if confidence_score is not None and not (0.0 <= confidence_score <= 1.0):
        errors.append("confidence_score:out_of_range")
    if liquidity_score is not None and not (0.0 <= liquidity_score <= 1.0):
        errors.append("liquidity_score:out_of_range")
    if fees_estimate is not None and fees_estimate < 0:
        errors.append("fees_estimate:negative")
    if slippage_estimate is not None and slippage_estimate < 0:
        errors.append("slippage_estimate:negative")
    if edge_percent is not None and abs(edge_percent) > 10_000:
        errors.append("edge_percent:abs_too_large")
    if edge_score is not None and abs(edge_score) > 10_000:
        errors.append("edge_score:abs_too_large")

    ok = (len(missing) == 0) and (len(errors) == 0)
    return CandidateValidationResult(ok=ok, missing_fields=missing, errors=errors)


def require_valid_candidate_for_execution(candidate: Any) -> CandidateValidationResult:
    """
    Execution authority gate (fail-closed):

    - Candidate must pass :func:`validate_candidate_fields`
    - Candidate must include `must_trade == True`
    """
    base = validate_candidate_fields(candidate)
    if not base.ok:
        return base
    raw: Optional[Mapping[str, Any]]
    if isinstance(candidate, UniversalGapCandidate):
        raw = candidate.to_dict()
    elif isinstance(candidate, Mapping):
        raw = candidate
    else:
        return CandidateValidationResult(ok=False, missing_fields=list(REQUIRED_CANDIDATE_FIELDS), errors=["candidate_type_invalid"])
    if raw.get("must_trade") is not True:
        return CandidateValidationResult(ok=False, missing_fields=[], errors=["must_trade:false"])
    return CandidateValidationResult(ok=True, missing_fields=[], errors=[])


def assert_no_candidate_defaults(candidate: Any, *, source_path: str) -> None:
    """
    Hard guard for live-capable paths: no silent fallbacks / defaults for required fields.
    We do not attempt to "guess" intent — if required fields are missing or invalid, raise.
    """
    res = require_valid_candidate_for_execution(candidate)
    if not res.ok:
        raise RuntimeError(
            "candidate_defaults_or_missing_fields:"
            + ",".join(res.missing_fields + res.errors)
            + f" source={source_path}"
        )

