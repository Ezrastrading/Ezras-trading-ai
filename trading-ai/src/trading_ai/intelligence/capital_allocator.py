"""Advisory capital weights across avenues — does not move funds or override limits."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _score_row(row: Dict[str, Any]) -> float:
    """Higher is better for allocation; penalize instability and drawdown."""
    verdict = str(row.get("verdict") or "weak")
    pnl_w = _f(row.get("pnl_week"))
    dd = _f(row.get("drawdown"))
    cons = row.get("consistency_score")
    ess = row.get("edge_stability_score")
    n = int(row.get("trade_count") or 0)

    base = 1.0 + max(-0.5, min(2.0, pnl_w / 500.0))
    if verdict == "unstable":
        base *= 0.15
    elif verdict == "weak":
        base *= 0.45
    elif verdict == "viable":
        base *= 1.0
    elif verdict == "strong":
        base *= 1.35

    base *= max(0.2, 1.0 - min(0.85, dd / 800.0))
    if cons is not None:
        base *= 0.55 + 0.45 * max(0.0, min(1.0, float(cons)))
    if ess is not None:
        base *= 0.65 + 0.35 * max(0.0, min(1.0, float(ess)))
    if n < 5:
        base *= 0.5
    return max(0.01, base)


def optimize_capital_allocation(
    system_state: Dict[str, Any],
    avenue_performance: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Returns allocation_map (sums to 1.0 when any positive weight), reasoning lines, risk_flags.

    Fail-safe: if no avenue looks viable, weights flatten with a ``reduce_deployment`` flag.
    """
    av = avenue_performance.get("avenues") if isinstance(avenue_performance.get("avenues"), dict) else {}
    if not av:
        return {
            "allocation_map": {},
            "reasoning": ["no_avenue_rows"],
            "risk_flags": ["insufficient_evidence_no_allocation"],
        }

    scores: List[Tuple[str, float]] = []
    for aid, row in av.items():
        if not isinstance(row, dict):
            continue
        scores.append((str(aid), _score_row(row)))

    scores.sort(key=lambda x: -x[1])
    raw = [max(0.0, s) for _, s in scores]
    ssum = sum(raw)
    risk_flags: List[str] = []
    reasoning: List[str] = []

    if ssum <= 0 or all(x < 0.02 for x in raw):
        n = len(scores)
        eq = 1.0 / n if n else 0.0
        out = {scores[i][0]: round(eq, 4) for i in range(n)}
        risk_flags.append("reduce_deployment_all_avenues_weak")
        reasoning.append("Equal split fail-safe — no avenue crossed minimum score threshold.")
        return {"allocation_map": out, "reasoning": reasoning, "risk_flags": risk_flags}

    weights = [r / ssum for r in raw]
    alloc = {scores[i][0]: round(weights[i], 4) for i in range(len(scores))}

    top = scores[0][0]
    reasoning.append(f"Largest weight to {top} by stability-adjusted weekly performance score.")
    if any(str(av.get(aid, {}).get("verdict")) == "unstable" for aid in alloc):
        risk_flags.append("unstable_avenue_present_downweight_via_scores")
    dq = (system_state.get("data_quality") or {}) if isinstance(system_state.get("data_quality"), dict) else {}
    if int(dq.get("trade_rows") or 0) < 8:
        risk_flags.append("global_trade_sample_still_low")
        reasoning.append("Low global trade count — allocation is exploratory only.")

    return {
        "allocation_map": alloc,
        "reasoning": reasoning,
        "risk_flags": risk_flags,
    }
