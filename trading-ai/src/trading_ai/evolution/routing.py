"""Evidence-aware capital routing across gates and defensive idle — bounded adaptation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from trading_ai.shark.coinbase_spot.capital_allocation import GateAllocationSplit, compute_gate_allocation_split
from trading_ai.evolution.measures import TradeSliceMetrics, compute_slice_metrics, filter_events


@dataclass(frozen=True)
class AdaptiveRoutingResult:
    split: GateAllocationSplit
    gate_a_score: float
    gate_b_score: float
    defensive_idle_fraction: float
    rationale: str
    evidence: Dict[str, Any]


def _relative_strength(a: TradeSliceMetrics, b: TradeSliceMetrics) -> float:
    """Return shift toward Gate B in [-0.2, 0.2] based on risk-adjusted net expectancy."""
    if a.n < 3 and b.n < 3:
        return 0.0
    sa = a.expectancy_net / (1.0 + (a.variance_pnl**0.5)) if a.n else 0.0
    sb = b.expectancy_net / (1.0 + (b.variance_pnl**0.5)) if b.n else 0.0
    diff = sb - sa
    return max(-0.2, min(0.2, diff * 8.0))


def compute_adaptive_gate_split(
    events: list,
    *,
    base_gate_a: float = 0.5,
    base_gate_b: float = 0.5,
    max_defensive_idle: float = 0.25,
    goal_urgency: float = 0.0,
) -> AdaptiveRoutingResult:
    """
    Start from defaults; nudge Gate A/B shares using measured net expectancy (not gross).

    ``goal_urgency`` in [0,1] reduces idle tilt when behind pace (still capped).
    """
    ga_events = filter_events(events, capital_gate="gate_a")
    gb_events = filter_events(events, capital_gate="gate_b")
    ma = compute_slice_metrics("gate_a", ga_events)
    mb = compute_slice_metrics("gate_b", gb_events)
    shift = _relative_strength(ma, mb)

    ga = max(0.15, min(0.85, base_gate_a - shift))
    gb = max(0.15, min(0.85, base_gate_b + shift))
    s = ga + gb
    ga, gb = ga / s, gb / s

    # Defensive idle rises when both gates show negative expectancy and elevated DD
    neg = (ma.expectancy_net < 0 or ma.n < 5) and (mb.expectancy_net < 0 or mb.n < 5)
    dd_stress = max(ma.max_drawdown, mb.max_drawdown) / max(1.0, abs(ma.net_pnl) + abs(mb.net_pnl) + 1.0)
    idle = max(0.0, min(max_defensive_idle, 0.08 + 0.5 * dd_stress))
    if neg and ma.n + mb.n > 10:
        idle = min(max_defensive_idle, idle + 0.12)
    idle *= max(0.35, 1.0 - 0.55 * max(0.0, min(1.0, goal_urgency)))

    rationale = (
        f"shift={shift:+.3f} from rolling net expectancy / variance; "
        f"idle={idle:.2%} stress={dd_stress:.3f}"
    )
    sp = compute_gate_allocation_split(gate_a_share=ga, gate_b_share=gb)
    return AdaptiveRoutingResult(
        split=sp,
        gate_a_score=max(0.0, ma.expectancy_net / (1.0 + ma.variance_pnl**0.5)) if ma.n else 0.0,
        gate_b_score=max(0.0, mb.expectancy_net / (1.0 + mb.variance_pnl**0.5)) if mb.n else 0.0,
        defensive_idle_fraction=idle,
        rationale=rationale,
        evidence={"gate_a": ma.to_dict(), "gate_b": mb.to_dict()},
    )


def routing_dict(res: AdaptiveRoutingResult) -> Dict[str, Any]:
    return {
        "gate_a_share": res.split.gate_a,
        "gate_b_share": res.split.gate_b,
        "within_gate_a_majors_share": res.split.gate_a_majors,
        "defensive_idle_fraction": res.defensive_idle_fraction,
        "rationale": res.rationale,
        "gate_a_score": res.gate_a_score,
        "gate_b_score": res.gate_b_score,
        "evidence": res.evidence,
    }
