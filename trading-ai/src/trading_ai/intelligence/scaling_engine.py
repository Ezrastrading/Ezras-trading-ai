"""Advisory scale posture from performance — does not resize live positions."""

from __future__ import annotations

from typing import Any, Dict, List


def _f(x: Any) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def generate_scaling_signal(
    system_state: Dict[str, Any],
    performance: Dict[str, Any],
) -> Dict[str, Any]:
    """
    ``performance`` may be the full bundle from global execution snapshot or
    ``{"avenue_performance": {...}, "capital_allocation": {...}}``.

    scale_factor: multiplicative hint around 1.0 (capped small — advisory).
    """
    ap = performance.get("avenue_performance") if isinstance(performance, dict) else {}
    if not isinstance(ap, dict):
        ap = {}
    av = ap.get("avenues") if isinstance(ap.get("avenues"), dict) else {}
    dq = system_state.get("data_quality") if isinstance(system_state.get("data_quality"), dict) else {}
    n_global = int(dq.get("trade_rows") or 0)

    reasons: List[str] = []
    unstable_n = sum(1 for row in av.values() if isinstance(row, dict) and str(row.get("verdict")) == "unstable")
    neg_weeks = sum(
        1 for row in av.values() if isinstance(row, dict) and _f(row.get("pnl_week")) < -25
    )

    dd = _f(system_state.get("max_drawdown"))
    wr = system_state.get("win_rate")
    wk = _f(system_state.get("weekly_pnl"))
    ess = system_state.get("edge_stability_score")

    scale_action = "hold"
    scale_factor = 1.0
    confidence = 0.35

    if n_global < 6:
        reasons.append("insufficient_trade_history_for_scaling_decision")
        return {
            "scale_action": "hold",
            "scale_factor": 1.0,
            "confidence": 0.25,
            "reason": "; ".join(reasons) or "hold_default",
        }

    if unstable_n >= 2 or neg_weeks >= 2 or (wr is not None and wr < 0.35 and n_global >= 10):
        scale_action = "decrease"
        scale_factor = 0.95
        confidence = 0.55
        reasons.append("multiple_weak_or_negative_avenue_weeks_or_low_win_rate")

    elif (
        wk > 0
        and dd < 300
        and (wr is None or wr >= 0.48)
        and unstable_n == 0
        and (ess is None or float(ess) >= 0.45)
        and n_global >= 12
    ):
        scale_action = "increase"
        scale_factor = 1.03
        confidence = 0.5
        reasons.append("positive_week_low_drawdown_acceptable_stability")

    else:
        reasons.append("default_hold_mixed_signals")

    if scale_action == "hold":
        confidence = max(confidence, 0.4)

    return {
        "scale_action": scale_action,
        "scale_factor": round(scale_factor, 4),
        "confidence": round(min(1.0, max(0.0, confidence)), 4),
        "reason": "; ".join(reasons) or "hold",
    }
