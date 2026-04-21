"""Hard composite quality score — drives eligibility signals (deterministic weights)."""

from __future__ import annotations

from typing import Any, Dict


def compute_bot_quality_contract(bot: Dict[str, Any]) -> Dict[str, Any]:
    sc = bot.get("promotion_scorecard") if isinstance(bot.get("promotion_scorecard"), dict) else {}
    perf = bot.get("performance") if isinstance(bot.get("performance"), dict) else {}

    def _f(k: str, d: float = 0.0) -> float:
        try:
            return float(sc.get(k) if k in sc else perf.get(k, d))
        except (TypeError, ValueError):
            return d

    expectancy = max(0.0, min(1.0, (_f("expectancy", 0.0) + 0.5) / 1.5))
    fill = max(0.0, 1.0 - min(1.0, _f("avg_slippage_bps", 0.0) / 200.0))
    truth_clean = max(0.0, 1.0 - min(1.0, float(sc.get("truth_conflict_unresolved") or 0) / 5.0))
    reliability = max(0.0, 1.0 - min(1.0, float(sc.get("duplicate_task_violations") or 0) / 10.0))
    cost_eff = max(0.0, min(1.0, 1.0 / (1.0 + float(perf.get("token_cost") or 0.0) / 1e6)))
    conflict = max(0.0, 1.0 - min(1.0, float(sc.get("truth_conflict_unresolved") or 0) / 3.0))
    stale = 1.0 if bot.get("last_heartbeat_at") else 0.0
    usefulness = float((perf.get("composite") or {}).get("utility_score") or 0.5)

    composite = (
        0.18 * expectancy
        + 0.12 * fill
        + 0.18 * truth_clean
        + 0.12 * reliability
        + 0.10 * cost_eff
        + 0.10 * conflict
        + 0.08 * stale
        + 0.12 * usefulness
    )
    return {
        "truth_version": "bot_quality_contract_v1",
        "components": {
            "profitability_expectancy": round(expectancy, 6),
            "fill_quality": round(fill, 6),
            "truth_cleanliness": round(truth_clean, 6),
            "task_reliability": round(reliability, 6),
            "cost_efficiency": round(cost_eff, 6),
            "conflict_rate_inverse": round(conflict, 6),
            "heartbeat_freshness": round(stale, 6),
            "usefulness": round(usefulness, 6),
        },
        "composite_quality": round(min(1.0, max(0.0, composite)), 6),
    }
