"""Post–Goal C: evidence-based next goals only (no vanity)."""

from __future__ import annotations

from typing import Any, Dict, List


def propose_post_c_goals(
    *,
    rolling_7d: float,
    rolling_30d: float,
    avenue_mix: Dict[str, float],
) -> List[Dict[str, Any]]:
    if rolling_7d < 2000:
        return []
    candidates: List[Dict[str, Any]] = []
    candidates.append(
        {
            "goal_id": "POST_C_WEEKLY_3K",
            "title": "Grow weekly net toward $3,000 after fees",
            "reason": "System already sustains $2K/week; scale quality before size.",
            "strategic_value": "high",
            "expected_benefit": "Higher recurring output with disciplined checks.",
            "activation_condition": "rolling_7d >= 2200 for 3 consecutive weeks",
            "priority_rank": 1,
        }
    )
    # Diversification if one avenue dominates
    if avenue_mix:
        mx = max(avenue_mix.values()) if avenue_mix.values() else 0
        total = sum(abs(v) for v in avenue_mix.values()) or 1.0
        if mx / total > 0.75:
            candidates.append(
                {
                    "goal_id": "POST_C_DIVERSIFY",
                    "title": "Cap single-avenue contribution under 60% of weekly net",
                    "reason": "Concentration risk detected in avenue mix.",
                    "strategic_value": "medium",
                    "expected_benefit": "Resilience to venue-specific drawdowns.",
                    "activation_condition": "avenue concentration > 60%",
                    "priority_rank": 2,
                }
            )
    candidates.append(
        {
            "goal_id": "POST_C_SHARPE",
            "title": "Improve consistency (lower weekly variance) while holding net",
            "reason": "After C, quality of equity curve matters.",
            "strategic_value": "medium",
            "expected_benefit": "Smoother compounding, fewer emotional overrides.",
            "activation_condition": "rolling_30d positive and stddev high vs mean",
            "priority_rank": 3,
        }
    )
    return candidates
