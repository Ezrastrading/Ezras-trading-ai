"""Daily / weekly marchboard rollup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report
from trading_ai.org_organism.io_utils import write_json_atomic
from trading_ai.org_organism.paths import daily_marchboard_path, weekly_marchboard_path
from trading_ai.org_organism.opportunity_pressure import build_opportunity_pressure_bundle
from trading_ai.org_organism.waste_detector import build_waste_detector_bundle
from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_marchboard(*, runtime_root: Path, weekly: bool = False) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    controlled = build_controlled_live_readiness_report(runtime_root=root, write_artifact=False)
    aut = build_autonomous_operator_path(runtime_root=root)
    opp = build_opportunity_pressure_bundle(runtime_root=root)
    waste = build_waste_detector_bundle(runtime_root=root)

    ra = (controlled.get("rollup_answers") or {}) if isinstance(controlled.get("rollup_answers"), dict) else {}
    top_goal = "supervised_confirmation_then_staged_automation" if not ra.get("is_avenue_a_supervised_live_ready") else "stabilize_truth_chain_then_scale_discipline"

    blockers = list(aut.get("active_blockers") or [])[:3]
    av_ops = [
        {"avenue": "A", "note": "coinbase_supervised_automation_path"},
        {"avenue": "B", "note": "kalshi_independent_queue"},
        {"avenue": "C", "note": "options_independent_queue"},
    ]
    gate_ops = [
        {"gate": "gate_a", "note": "strict_proof_and_selection"},
        {"gate": "gate_b", "note": "momentum_lane_and_micro_proof"},
    ]
    exps = (opp.get("experiment_priority_queue") or {}).get("ranked") or []
    top_ex = exps[:3]
    raw_ds = waste.get("drag_sources")
    if isinstance(raw_ds, list):
        waste_src = raw_ds
    elif isinstance(raw_ds, dict):
        waste_src = list(raw_ds.get("sources") or [])
    else:
        waste_src = []
    top_waste = waste_src[:3]

    payload: Dict[str, Any] = {
        "truth_version": "daily_marchboard_v1" if not weekly else "weekly_marchboard_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "period": "weekly" if weekly else "daily",
        "top_active_goal": top_goal,
        "top_3_blockers": blockers,
        "top_3_avenue_opportunities": av_ops,
        "top_3_gate_opportunities": gate_ops,
        "top_3_experiments": top_ex,
        "top_waste_sources": top_waste,
        "top_operational_fixes": [
            "refresh_runtime_artifacts",
            "controlled_live_readiness",
            "clear_duplicate_blockers_in_operator_path",
        ],
        "current_avenue_standings": opp.get("avenue_priority_queue"),
        "today_plan": [
            "Run mission-execution-status",
            "Run supervised-readiness-closer if trading session",
            "Append experiment results after any harness",
        ],
        "tomorrow_plan": [
            "Recompute autonomous-gap-closer",
            "Review bot-scorecard-report for stale bots",
        ],
        "what_would_most_improve_next_24h": "One fresh end-to-end proof run with clean artifact refresh — not more parameters.",
        "honesty": "Standings are qualitative priority hints from artifacts — not performance rankings.",
    }
    dest = weekly_marchboard_path(root) if weekly else daily_marchboard_path(root)
    write_json_atomic(dest, payload)
    return payload
