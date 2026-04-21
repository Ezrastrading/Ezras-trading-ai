"""Capital accumulation view — compounding, contributions, pace vs goal."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from trading_ai.shark.growth_tracker import MONTHLY_TARGETS, get_growth_status
from trading_ai.evolution.measures import infer_capital_gate


def contribution_by_dimension(events: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Net PnL attribution by avenue and inferred gate (best-effort)."""
    by_avenue: Dict[str, float] = {}
    by_gate: Dict[str, float] = {"gate_a": 0.0, "gate_b": 0.0, "unknown": 0.0}
    by_edge: Dict[str, float] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        net = float(ev.get("net_pnl") or ev.get("net_pnl_usd") or 0.0)
        an = str(ev.get("avenue_name") or "unknown").lower()
        by_avenue[an] = by_avenue.get(an, 0.0) + net
        g = infer_capital_gate(ev)
        by_gate[g] = by_gate.get(g, 0.0) + net
        eid = str(ev.get("edge_id") or "").strip()
        if eid:
            by_edge[eid] = by_edge.get(eid, 0.0) + net
    return {
        "by_avenue": by_avenue,
        "by_gate": by_gate,
        "by_edge": by_edge,
    }


def accumulation_snapshot(
    *,
    current_capital: float,
    events: List[Mapping[str, Any]],
    year_end_target: Optional[float] = None,
) -> Dict[str, Any]:
    growth = get_growth_status(current_capital)
    contrib = contribution_by_dimension(events)
    ytarget = year_end_target if year_end_target is not None else MONTHLY_TARGETS[-1][1]
    pace = growth.get("trajectory", "unknown")
    gap = float(ytarget) - float(current_capital)
    return {
        "current_capital": current_capital,
        "growth_tracker": growth,
        "year_end_target": ytarget,
        "gap_to_year_end": gap,
        "pace_label": pace,
        "contributions": contrib,
    }


def goal_pace_evaluation(
    current_capital: float,
    *,
    days_elapsed: Optional[int] = None,
) -> Dict[str, Any]:
    """Ahead / on-track / behind vs minimum path (uses growth_tracker)."""
    st = get_growth_status(current_capital, days_elapsed=days_elapsed)
    return {
        "trajectory": st.get("trajectory"),
        "on_pace": st.get("on_pace"),
        "projected_month_end": st.get("projected_month_end"),
        "which_target_on_pace_for": st.get("which_target_on_pace_for"),
    }
