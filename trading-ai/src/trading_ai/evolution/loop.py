"""
First-class automation loop: discover → route → measure → score → adjust → report → repeat.

Execution of live trades is delegated to existing runners; this layer orchestrates evidence and policy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.edge.promotion_runner import run_promotion_cycle
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.evolution.accumulation import accumulation_snapshot, goal_pace_evaluation
from trading_ai.evolution.acceleration import evaluate_goal_acceleration
from trading_ai.evolution.adjustments import apply_automated_adjustments, suggest_adjustments
from trading_ai.evolution.outputs import evolution_report_paths, write_evolution_artifacts
from trading_ai.evolution.routing import compute_adaptive_gate_split, routing_dict
from trading_ai.evolution.safest import rank_safest_edges, safest_venue
from trading_ai.evolution.scoring import rank_edges_by_score

logger = logging.getLogger(__name__)


def run_evolution_cycle(
    events: Optional[List[Mapping[str, Any]]] = None,
    *,
    registry: Optional[EdgeRegistry] = None,
    current_capital: Optional[float] = None,
    write_artifacts: bool = True,
    apply_adjustments: bool = True,
) -> Dict[str, Any]:
    """
    Single inspectable cycle of the build → trade → measure → adjust loop.

    Pass ``events`` from :func:`trading_ai.nte.databank.local_trade_store.load_all_trade_events`
    or supply a test list. Loads events if omitted.
    """
    if events is None:
        from trading_ai.nte.databank.local_trade_store import load_all_trade_events

        events = load_all_trade_events()
    ev_list = list(events)
    reg = registry or EdgeRegistry()

    if current_capital is None:
        try:
            from trading_ai.shark.state_store import load_capital

            current_capital = float(load_capital().current_capital)
        except Exception:
            current_capital = 0.0

    step1 = {
        "step": 1,
        "name": "discover_hypotheses",
        "edges_registered": [e.edge_id for e in reg.list_edges()],
        "note": "Hypotheses materialize via research_bridge / operator; registry is source of truth.",
    }

    routing = compute_adaptive_gate_split(ev_list, goal_urgency=0.2 if current_capital else 0.0)
    step2 = {
        "step": 2,
        "name": "route_small_capital_testing",
        "routing": routing_dict(routing),
    }

    step3 = {
        "step": 3,
        "name": "execute_trades",
        "delegated_to": ["nte.coinbase_engine", "shark.run_shark", "execution_chain"],
    }

    step4 = {
        "step": 4,
        "name": "measure_real_outcomes",
        "trade_event_count": len(ev_list),
    }

    ranked = rank_edges_by_score(reg.list_edges(), ev_list)
    step5 = {
        "step": 5,
        "name": "score_results",
        "ranked_edges": ranked,
    }

    step6 = {
        "step": 6,
        "name": "adjust_ranking_allocation_confidence",
        "routing_followup": routing_dict(routing),
        "suggestions": suggest_adjustments(ev_list, registry=reg),
    }

    promo = run_promotion_cycle(ev_list, registry=reg)
    step7 = {
        "step": 7,
        "name": "promote_winners_registry",
        "promotion_cycle": promo,
    }

    adj = apply_automated_adjustments(
        ev_list,
        registry=reg,
        apply_pauses=apply_adjustments,
        apply_scaled_promotion=apply_adjustments,
    )
    step8 = {
        "step": 8,
        "name": "reduce_or_pause_losers",
        "automated_adjustments": adj,
    }

    acc = accumulation_snapshot(current_capital=current_capital, events=ev_list)
    accel = evaluate_goal_acceleration(ev_list, current_capital=current_capital, ranked_edges=ranked, routing=routing)
    safest = rank_safest_edges(ev_list, registry_edges=reg.list_edges())
    sv = safest_venue(ev_list)

    step9 = {
        "step": 9,
        "name": "update_operator_ceo_sessions",
        "accumulation": acc,
        "acceleration": accel,
        "safest_edges": safest[:12],
        "safest_venue": sv,
        "goal_pace": goal_pace_evaluation(current_capital),
    }

    step10 = {
        "step": 10,
        "name": "repeat",
        "next_run_hint": "invoke on schedule or after each closed-trade batch; no guaranteed profitability.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    bundle = {
        "schema": "ezras.evolution_cycle.v1",
        "steps": [step1, step2, step3, step4, step5, step6, step7, step8, step9, step10],
        "summary": {
            "top_edges": ranked[:5],
            "safest_edges": safest[:5],
            "highest_roi_proxy": ranked[:5],
            "most_degraded": list(reversed(ranked[-5:])) if ranked else [],
            "gate_split": routing_dict(routing),
        },
    }

    if write_artifacts:
        try:
            paths = write_evolution_artifacts(bundle)
            bundle["artifacts"] = paths
        except Exception as exc:
            logger.warning("evolution artifacts: %s", exc)
            bundle["artifacts_error"] = str(exc)

    return bundle
