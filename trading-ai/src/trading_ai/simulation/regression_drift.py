"""Compare recent vs baseline windows on a numeric series; drive corrective task hints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mean(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / len(xs)


def compare_recent_vs_baseline(
    series: List[float],
    *,
    recent_n: int = 3,
    baseline_n: int = 3,
    degrade_threshold: float = 5.0,
    improve_threshold: float = 5.0,
) -> Dict[str, Any]:
    """
    Compare mean(recent window) vs mean(older baseline window immediately before recent).

    Emits ``emit_corrective_tasks`` when recent is materially worse than baseline.
    """
    vals = [float(x) for x in series]
    need = recent_n + baseline_n
    if len(vals) < need:
        return {
            "truth_version": "sim_regression_drift_v1",
            "generated_at": _iso(),
            "verdict": "insufficient_history",
            "emit_corrective_tasks": False,
            "recent_mean": None,
            "baseline_mean": None,
            "points_used": len(vals),
        }
    recent = vals[-recent_n:]
    baseline = vals[-(recent_n + baseline_n) : -recent_n]
    rm = _mean(recent)
    bm = _mean(baseline)
    assert rm is not None and bm is not None
    delta = rm - bm
    verdict = "stable"
    emit = False
    if delta < -degrade_threshold:
        verdict = "degrading"
        emit = True
    elif delta > improve_threshold:
        verdict = "improving"
    payload: Dict[str, Any] = {
        "truth_version": "sim_regression_drift_v1",
        "generated_at": _iso(),
        "verdict": verdict,
        "emit_corrective_tasks": emit,
        "recent_mean": round(rm, 6),
        "baseline_mean": round(bm, 6),
        "delta_recent_minus_baseline": round(delta, 6),
        "recent_n": recent_n,
        "baseline_n": baseline_n,
        "points_used": len(vals),
    }
    return payload


def extract_net_points_from_history(history: List[Dict[str, Any]], key: str = "net_session_usd") -> List[float]:
    out: List[float] = []
    for h in history:
        if not isinstance(h, dict):
            continue
        v = h.get(key)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out
