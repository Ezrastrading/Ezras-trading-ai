"""
Structured CEO sessions: Gate A, Gate B, and global capital / risk — advisory JSON for reports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


def build_evolution_ceo_answers(ev_bundle: Mapping[str, Any]) -> Dict[str, Any]:
    """Structured answers for CEO_GLOBAL_SESSION from one evolution cycle (advisory)."""
    summ = ev_bundle.get("summary") or {}
    top = summ.get("top_edges") or []
    safest = summ.get("safest_edges") or []
    degraded = summ.get("most_degraded") or []
    steps = ev_bundle.get("steps") or []
    accel: Dict[str, Any] = {}
    acc: Dict[str, Any] = {}
    for st in steps:
        if not isinstance(st, dict):
            continue
        if st.get("name") == "update_operator_ceo_sessions":
            accel = st.get("acceleration") or {}
            acc = st.get("accumulation") or {}
    routing = summ.get("gate_split") or {}
    return {
        "what_is_working": [e.get("edge_id") for e in top[:5]],
        "what_is_failing": [e.get("edge_id") for e in degraded[:5]],
        "safest_edges_now": [e.get("edge_id") for e in safest[:5]],
        "highest_score_edges_now": [e.get("edge_id") for e in top[:5]],
        "most_degraded_edges_now": [e.get("edge_id") for e in degraded[:5]],
        "capital_routing": routing,
        "goal_acceleration": accel.get("growth_mode_recommendation"),
        "more_allocation": accel.get("more_allocation_candidates"),
        "less_allocation": accel.get("reduce_allocation_candidates"),
        "accumulation_snapshot": acc,
        "disclaimer": "Advisory only — not a guarantee of profit; governance and safety systems remain authoritative.",
    }


def build_ceo_a_session(metrics_gate_a: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    m = dict(metrics_gate_a or {})
    return {
        "session": "CEO_A_SESSION",
        "focus": "Gate A — spot core / slower strategies",
        "metrics": m,
        "checks": [
            "BTC/ETH sleeve vs other alts allocation discipline",
            "execution quality and slippage vs fees",
            "reconciliation and inventory truth",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_ceo_b_session(metrics_gate_b: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    m = dict(metrics_gate_b or {})
    return {
        "session": "CEO_B_SESSION",
        "focus": "Gate B — momentum / gainer lane",
        "metrics": m,
        "checks": [
            "false breakout rate and continuation quality",
            "trailing stop and peak discipline",
            "drawdown vs profit target after costs",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_ceo_global_session(
    *,
    gate_a: Optional[Mapping[str, Any]] = None,
    gate_b: Optional[Mapping[str, Any]] = None,
    capital_split: Optional[Mapping[str, Any]] = None,
    evolution_bundle: Optional[Mapping[str, Any]] = None,
    execution_intelligence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "session": "CEO_GLOBAL_SESSION",
        "focus": "Capital allocation and cross-gate risk",
        "gate_a_snapshot": dict(gate_a or {}),
        "gate_b_snapshot": dict(gate_b or {}),
        "capital_split": dict(capital_split or {}),
        "recommendations_placeholder": [
            "Compare Gate A vs Gate B post-fee expectancy over rolling windows.",
            "Reduce Gate B size if daily drawdown or loss streak thresholds trip.",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if evolution_bundle:
        out["evolution_loop"] = dict(evolution_bundle)
        out["evolution_ceo_answers"] = build_evolution_ceo_answers(evolution_bundle)
    if execution_intelligence:
        eie = dict(execution_intelligence)
        prog = eie.get("progress") or {}
        plan = eie.get("daily_plan") or {}
        out["execution_intelligence"] = {
            "active_goal": eie.get("active_goal"),
            "progress_summary": {
                "progress_pct": prog.get("progress_pct"),
                "trajectory_status": prog.get("trajectory_status"),
                "current_position": prog.get("current_position"),
                "estimated_days_remaining": prog.get("estimated_days_remaining"),
            },
            "blockers": prog.get("blockers") or [],
            "strengths": prog.get("strengths") or [],
            "today_plan": plan.get("today_focus") or [],
            "tomorrow_plan": plan.get("tomorrow_focus") or [],
            "improvement_focus": plan.get("priority_actions") or [],
            "execution_constraints": plan.get("execution_constraints") or [],
            "avoid_actions": plan.get("avoid_actions") or [],
            "mode": plan.get("mode"),
            "disclaimer": plan.get("disclaimer"),
        }
    return out


def run_ceo_session_bundle(
    *,
    metrics_gate_a: Optional[Mapping[str, Any]] = None,
    metrics_gate_b: Optional[Mapping[str, Any]] = None,
    capital_split: Optional[Mapping[str, Any]] = None,
    operating_mode_report: Optional[Mapping[str, Any]] = None,
    include_evolution: bool = False,
    evolution_bundle: Optional[Mapping[str, Any]] = None,
    execution_intelligence_bundle: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Single call for command-center / diagnosis extensions."""
    ev = evolution_bundle
    if ev is None and include_evolution:
        try:
            from trading_ai.evolution.loop import run_evolution_cycle

            ev = run_evolution_cycle(write_artifacts=False, apply_adjustments=False)
        except Exception:
            ev = None
    eie = execution_intelligence_bundle
    if eie is None:
        try:
            from trading_ai.intelligence.execution_intelligence.persistence import refresh_execution_intelligence

            eie = refresh_execution_intelligence(persist=False)
        except Exception:
            eie = None
    global_payload = build_ceo_global_session(
        gate_a=metrics_gate_a,
        gate_b=metrics_gate_b,
        capital_split=capital_split,
        evolution_bundle=ev,
        execution_intelligence=eie,
    )
    if operating_mode_report:
        global_payload["adaptive_operating_system"] = dict(operating_mode_report)
        global_payload["emergency_brake_status"] = operating_mode_report.get("emergency_brake_triggered")
        global_payload["recovery_readiness"] = {
            "restart_ready": operating_mode_report.get("restart_ready"),
            "confidence_scaling_ready": operating_mode_report.get("confidence_scaling_ready"),
        }
    return {
        "CEO_A_SESSION": build_ceo_a_session(metrics_gate_a),
        "CEO_B_SESSION": build_ceo_b_session(metrics_gate_b),
        "CEO_GLOBAL_SESSION": global_payload,
    }
