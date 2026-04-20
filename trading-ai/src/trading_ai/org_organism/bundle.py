"""Write all organism artifacts in one deterministic pass."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from trading_ai.org_organism.autonomous_gap_closer import build_autonomous_gap_bundle
from trading_ai.org_organism.bot_scorecard import build_bot_scorecard_bundle
from trading_ai.org_organism.experiment_os import load_experiment_registry, recompute_summaries
from trading_ai.org_organism.first_supervised_cc import build_first_supervised_command_center
from trading_ai.org_organism.gate_b_readiness import build_gate_b_readiness_report
from trading_ai.org_organism.marchboard import build_marchboard
from trading_ai.org_organism.mission_execution_layer import build_mission_execution_bundle
from trading_ai.org_organism.opportunity_pressure import build_opportunity_pressure_bundle
from trading_ai.org_organism.supervised_readiness import build_supervised_readiness_closer, build_supervised_sequence_plan
from trading_ai.org_organism.waste_detector import build_waste_detector_bundle


def write_full_organism_bundle(*, runtime_root: Path, registry_path: Path | None = None) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    reg = load_experiment_registry(root)
    open_exp = sum(
        1
        for e in (reg.get("experiments") or {}).values()
        if isinstance(e, dict) and str(e.get("status") or "") not in ("passed", "superseded", "")
    )
    recompute_summaries(root)

    mission = build_mission_execution_bundle(runtime_root=root, experiment_open_count=open_exp)
    opp = build_opportunity_pressure_bundle(runtime_root=root)
    waste = build_waste_detector_bundle(runtime_root=root)
    score = build_bot_scorecard_bundle(runtime_root=root, registry_path=registry_path)
    sup_c = build_supervised_readiness_closer(runtime_root=root)
    sup_p = build_supervised_sequence_plan(runtime_root=root)
    gap = build_autonomous_gap_bundle(runtime_root=root)
    fs = build_first_supervised_command_center(runtime_root=root)
    gb = build_gate_b_readiness_report(runtime_root=root)
    daily = build_marchboard(runtime_root=root, weekly=False)
    weekly = build_marchboard(runtime_root=root, weekly=True)

    return {
        "ok": True,
        "runtime_root": str(root),
        "mission_execution": mission,
        "opportunity_pressure": opp,
        "waste_detector": waste,
        "bot_scorecard": score,
        "supervised_readiness_closer": sup_c,
        "supervised_sequence_plan": sup_p,
        "autonomous_gap": gap,
        "first_supervised_cc": fs,
        "gate_b_readiness": gb,
        "daily_marchboard": daily,
        "weekly_marchboard": weekly,
    }
