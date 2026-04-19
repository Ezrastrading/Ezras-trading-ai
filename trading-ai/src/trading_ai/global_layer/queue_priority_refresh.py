"""Apply :mod:`trading_ai.global_layer.governance_formulas` scores to review queue ordering."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from trading_ai.global_layer.governance_formulas import (
    candidate_priority_score,
    ceo_review_priority_score,
    promotion_gates_ok,
    promotion_priority_score,
    risk_reduction_priority_score,
    speed_to_goal_priority_score,
)
from trading_ai.global_layer.review_storage import ReviewStorage

logger = logging.getLogger(__name__)


def _f(x: Any, default: float) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def score_candidate_item(it: Dict[str, Any]) -> float:
    return candidate_priority_score(
        {
            "post_fee_expectancy_score": _f(it.get("post_fee_expectancy_score"), 45.0),
            "risk_reduction_score": _f(it.get("risk_reduction_score"), 40.0),
            "execution_cleanliness_score": _f(it.get("execution_cleanliness_score"), 50.0),
            "sample_quality_score": _f(it.get("sample_quality_score"), 45.0),
            "path_to_goal_relevance_score": _f(it.get("path_to_goal_relevance"), _f(it.get("path_to_goal_relevance_score"), 45.0)),
            "scalability_score": _f(it.get("scalability_score"), 40.0),
            "fragility_penalty": _f(it.get("fragility_penalty"), 10.0),
            "novelty_penalty": _f(it.get("novelty_penalty"), 5.0),
            "current_risk_regime_penalty": _f(it.get("current_risk_regime_penalty"), 5.0),
        }
    )


def score_promotion_item(it: Dict[str, Any]) -> float:
    return promotion_priority_score(
        {
            "candidate_priority_score": _f(it.get("candidate_priority_score"), score_candidate_item(it)),
            "shadow_validation_score": _f(it.get("shadow_validation_score"), 50.0),
            "drawdown_acceptability_score": _f(it.get("drawdown_acceptability_score"), 55.0),
            "promotion_readiness_score": _f(it.get("promotion_readiness_score"), 50.0),
            "governance_clearance_score": _f(it.get("governance_clearance_score"), 60.0),
            "live_regime_penalty": _f(it.get("live_regime_penalty"), 10.0),
            "model_disagreement_penalty": _f(it.get("model_disagreement_penalty"), 5.0),
        }
    )


def score_risk_reduction_item(it: Dict[str, Any]) -> float:
    esc = bool(it.get("priority_boost") or it.get("escalation_applies"))
    return risk_reduction_priority_score(
        {
            "risk_severity_score": _f(it.get("risk_severity_score"), 55.0),
            "repeat_frequency_score": _f(it.get("repeat_frequency_score"), 40.0),
            "capital_protection_score": _f(it.get("capital_protection_score"), 50.0),
            "implementation_ease_score": _f(it.get("implementation_ease_score"), 45.0),
            "fragility_reduction_score": _f(it.get("fragility_reduction_score"), 45.0),
            "speed_to_value_score": _f(it.get("speed_to_value_score"), 40.0),
            "evidence_strength_score": _f(it.get("evidence_strength_score"), 45.0),
            "escalation_applies": esc,
        }
    )


def score_ceo_item(it: Dict[str, Any]) -> float:
    pr = str(it.get("priority") or "medium").lower()
    urg = 50.0
    if pr == "high":
        urg = 75.0
    elif pr == "low":
        urg = 25.0
    return ceo_review_priority_score(
        {
            "capital_impact_score": _f(it.get("capital_impact_score"), 50.0),
            "strategic_relevance_score": _f(it.get("strategic_relevance_score"), 50.0),
            "urgency_score": _f(it.get("urgency_score"), urg),
            "path_to_goal_relevance_score": _f(it.get("path_to_goal_relevance_score"), 50.0),
            "model_disagreement_score": _f(it.get("model_disagreement_score"), 20.0),
            "governance_severity_score": _f(it.get("governance_severity_score"), 40.0),
        }
    )


def _sort_items(items: List[Any], scorer) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        row = dict(it)
        s = scorer(row)
        row["governance_priority_score"] = round(s, 4)
        out.append(row)
    out.sort(key=lambda x: float(x.get("governance_priority_score") or 0.0), reverse=True)
    return out


def refresh_queue_priorities(storage: ReviewStorage) -> None:
    """Re-score and persist queue JSON files (deterministic sort by governance formula)."""
    try:
        joint = storage.load_json("joint_review_latest.json")
        live = str(joint.get("live_mode_recommendation") or "").strip().lower()
        paused_live = live == "paused"
        ver_fail = bool(joint.get("review_integrity_state") and str(joint.get("review_integrity_state")) != "full")
        hard_cluster = False
        hv = joint.get("house_view") or {}
        risks = " ".join(hv.get("top_risk_issues") or []).lower()
        if "verification" in risks or "hard_stop" in risks:
            hard_cluster = True
    except Exception:
        paused_live, ver_fail, hard_cluster = False, False, False

    try:
        cq = storage.load_json("candidate_queue.json")
        items = _sort_items(list(cq.get("items") or []), score_candidate_item)
        cq["items"] = items[-200:]
        cq["governance_scoring"] = "candidate_priority_score"
        storage.save_json("candidate_queue.json", cq)

        pq = storage.load_json("promotion_queue.json")
        raw_pi = [x for x in (pq.get("items") or []) if isinstance(x, dict)]
        scored_pi: List[Dict[str, Any]] = []
        for it in raw_pi:
            row = dict(it)
            cps = _f(row.get("candidate_priority_score"), score_candidate_item(row))
            svs = _f(row.get("shadow_validation_score"), 50.0)
            dda = _f(row.get("drawdown_acceptability_score"), 55.0)
            gcs = _f(row.get("governance_clearance_score"), 60.0)
            row["promotion_gates_ok"] = promotion_gates_ok(
                candidate_priority_score=cps,
                shadow_validation_score=svs,
                drawdown_acceptability_score=dda,
                governance_clearance_score=gcs,
                paused_live_mode=paused_live,
                verification_failure=ver_fail,
                hard_stop_cluster=hard_cluster,
            )
            row["governance_priority_score"] = round(score_promotion_item(row), 4)
            scored_pi.append(row)
        scored_pi.sort(key=lambda x: float(x.get("governance_priority_score") or 0.0), reverse=True)
        pq["items"] = scored_pi[-200:]
        pq["governance_scoring"] = "promotion_priority_score"
        pq["promotion_gates_context"] = {
            "paused_live_mode": paused_live,
            "verification_failure": ver_fail,
            "hard_stop_cluster": hard_cluster,
        }
        storage.save_json("promotion_queue.json", pq)

        rq = storage.load_json("risk_reduction_queue.json")
        ritems = _sort_items(list(rq.get("items") or []), score_risk_reduction_item)
        rq["items"] = ritems[-300:]
        rq["governance_scoring"] = "risk_reduction_priority_score"
        storage.save_json("risk_reduction_queue.json", rq)

        ce = storage.load_json("ceo_review_queue.json")
        citems = _sort_items(list(ce.get("items") or []), score_ceo_item)
        ce["items"] = citems[-100:]
        ce["governance_scoring"] = "ceo_review_priority_score"
        storage.save_json("ceo_review_queue.json", ce)

        sp = storage.load_json("speed_to_goal_review.json")
        # speed_to_goal is mostly summary; store a computed headline score for visibility
        summary = str(sp.get("summary") or "")
        sp["governance_headline_score"] = round(
            speed_to_goal_priority_score(
                {
                    "growth_rate_improvement_score": 45.0,
                    "scalability_score": 45.0,
                    "repeatability_score": 45.0,
                    "execution_reliability_score": 50.0,
                    "survival_compatibility_score": 50.0,
                    "evidence_strength_score": 40.0,
                    "drawdown_burden_score": 30.0 if "risk" in summary.lower() else 15.0,
                }
            ),
            4,
        )
        sp["governance_scoring"] = "speed_to_goal_priority_score"
        storage.save_json("speed_to_goal_review.json", sp)
    except Exception as exc:
        logger.warning("refresh_queue_priorities: %s", exc)
