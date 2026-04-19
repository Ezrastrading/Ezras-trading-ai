"""Hard-number governance: queue priorities, anomaly defaults, scheduler defaults."""

from __future__ import annotations

from typing import Any, Dict, Tuple


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def clamp100(x: float) -> float:
    return max(0.0, min(100.0, x))


def priority_label(score: float) -> str:
    s = clamp100(score)
    if s <= 24:
        return "low"
    if s <= 49:
        return "medium"
    if s <= 74:
        return "high"
    return "critical"


def candidate_priority_score(inputs: Dict[str, float]) -> float:
    """Candidate queue formula (0–100)."""
    p = (
        0.24 * inputs.get("post_fee_expectancy_score", 0)
        + 0.16 * inputs.get("risk_reduction_score", 0)
        + 0.12 * inputs.get("execution_cleanliness_score", 0)
        + 0.14 * inputs.get("sample_quality_score", 0)
        + 0.14 * inputs.get("path_to_goal_relevance_score", 0)
        + 0.10 * inputs.get("scalability_score", 0)
        - 0.05 * inputs.get("fragility_penalty", 0)
        - 0.03 * inputs.get("novelty_penalty", 0)
        - 0.02 * inputs.get("current_risk_regime_penalty", 0)
    )
    return clamp100(p)


def promotion_priority_score(inputs: Dict[str, float]) -> float:
    """Promotion queue formula."""
    p = (
        0.28 * inputs.get("candidate_priority_score", 0)
        + 0.22 * inputs.get("shadow_validation_score", 0)
        + 0.18 * inputs.get("drawdown_acceptability_score", 0)
        + 0.16 * inputs.get("promotion_readiness_score", 0)
        + 0.10 * inputs.get("governance_clearance_score", 0)
        - 0.04 * inputs.get("live_regime_penalty", 0)
        - 0.02 * inputs.get("model_disagreement_penalty", 0)
    )
    return clamp100(p)


def risk_reduction_priority_score(inputs: Dict[str, float], *, escalation_bonus: float = 10.0) -> float:
    """Risk reduction queue formula; optional escalation bonus (capped at 100)."""
    p = (
        0.24 * inputs.get("risk_severity_score", 0)
        + 0.16 * inputs.get("repeat_frequency_score", 0)
        + 0.20 * inputs.get("capital_protection_score", 0)
        + 0.10 * inputs.get("implementation_ease_score", 0)
        + 0.14 * inputs.get("fragility_reduction_score", 0)
        + 0.08 * inputs.get("speed_to_value_score", 0)
        + 0.08 * inputs.get("evidence_strength_score", 0)
    )
    if inputs.get("escalation_applies"):
        p += escalation_bonus
    return clamp100(p)


def ceo_review_priority_score(inputs: Dict[str, float]) -> float:
    """CEO review queue formula."""
    p = (
        0.22 * inputs.get("capital_impact_score", 0)
        + 0.18 * inputs.get("strategic_relevance_score", 0)
        + 0.18 * inputs.get("urgency_score", 0)
        + 0.18 * inputs.get("path_to_goal_relevance_score", 0)
        + 0.10 * inputs.get("model_disagreement_score", 0)
        + 0.14 * inputs.get("governance_severity_score", 0)
    )
    return clamp100(p)


def speed_to_goal_priority_score(inputs: Dict[str, float]) -> float:
    """Speed-to-goal priority formula."""
    p = (
        0.24 * inputs.get("growth_rate_improvement_score", 0)
        + 0.16 * inputs.get("scalability_score", 0)
        + 0.16 * inputs.get("repeatability_score", 0)
        + 0.14 * inputs.get("execution_reliability_score", 0)
        + 0.14 * inputs.get("survival_compatibility_score", 0)
        + 0.10 * inputs.get("evidence_strength_score", 0)
        - 0.10 * inputs.get("drawdown_burden_score", 0)
    )
    return clamp100(p)


def anomaly_severity_label(severity: float) -> str:
    s = clamp100(severity)
    if s <= 24:
        return "minor"
    if s <= 49:
        return "moderate"
    if s <= 74:
        return "major"
    return "critical"


# Production defaults (governance spec section 20)
PRODUCTION_DEFAULTS: Dict[str, Any] = {
    "market_ws_warning_sec": 15,
    "market_ws_major_sec": 30,
    "market_ws_critical_sec": 60,
    "user_ws_warning_sec": 20,
    "user_ws_major_sec": 45,
    "user_ws_critical_sec": 90,
    "exception_review_cooldown_min": 45,
    "max_reviews_per_day": 4,
    "midday_min_closed_trades": 3,
    "midday_min_shadow_candidates": 5,
    "joint_confidence_caution_threshold": 0.55,
    "joint_confidence_pause_attention_threshold": 0.40,
    "promotion_min_priority": 65,
    "risk_reduction_escalation_bonus": 10,
}


def sample_strength_from_trade_count(n: int) -> float:
    """Live trade sample count to sample strength (0–100)."""
    if n <= 2:
        return 15.0
    if n <= 5:
        return 30.0
    if n <= 10:
        return 50.0
    if n <= 20:
        return 70.0
    if n <= 40:
        return 85.0
    return 100.0


def promotion_gates_ok(
    *,
    candidate_priority_score: float,
    shadow_validation_score: float,
    drawdown_acceptability_score: float,
    governance_clearance_score: float,
    paused_live_mode: bool,
    verification_failure: bool,
    hard_stop_cluster: bool,
) -> bool:
    """Promotion gates (all must pass)."""
    if candidate_priority_score < 60:
        return False
    if shadow_validation_score < 65:
        return False
    if drawdown_acceptability_score < 60:
        return False
    if governance_clearance_score < 70:
        return False
    if paused_live_mode:
        return False
    if verification_failure:
        return False
    if hard_stop_cluster:
        return False
    return True


def ws_stale_severity_market(seconds_stale: float) -> Tuple[float, str]:
    """Market WS stale: severity score and label."""
    w, mj, cr = 15, 30, 60
    if seconds_stale <= w:
        return 0.0, "minor"
    if seconds_stale <= mj:
        return 35.0, "moderate"
    if seconds_stale <= cr:
        return 60.0, "major"
    return 85.0, "critical"


def ws_stale_severity_user(seconds_stale: float) -> Tuple[float, str]:
    """User WS stale: severity score and label."""
    w, mj, cr = 20, 45, 90
    if seconds_stale <= w:
        return 0.0, "minor"
    if seconds_stale <= mj:
        return 30.0, "moderate"
    if seconds_stale <= cr:
        return 55.0, "major"
    return 80.0, "critical"
