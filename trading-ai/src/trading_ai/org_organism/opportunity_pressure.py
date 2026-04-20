"""Ranks where marginal attention should go — qualitative, bounded, honest."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report
from trading_ai.org_organism.experiment_os import load_experiment_registry
from trading_ai.org_organism.io_utils import write_json_atomic
from trading_ai.org_organism.paths import (
    avenue_priority_queue_path,
    blocker_priority_queue_path,
    experiment_priority_queue_path,
    gate_priority_queue_path,
    opportunity_pressure_snapshot_path,
)
from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score_blocker(b: str) -> float:
    """Higher = more urgent to remove (heuristic)."""
    low = b.lower()
    if "credential" in low or "ssl" in low:
        return 1.0
    if "supabase" in low or "schema" in low:
        return 0.95
    if "halt" in low or "brake" in low:
        return 0.9
    if "proof" in low or "consistent" in low:
        return 0.85
    return 0.5


def build_opportunity_pressure_bundle(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    controlled = build_controlled_live_readiness_report(runtime_root=root, write_artifact=False)
    aut = build_autonomous_operator_path(runtime_root=root)
    reg = load_experiment_registry(root)
    exps = list((reg.get("experiments") or {}).values())

    avenues: List[Tuple[str, float, str]] = []
    ra = (controlled.get("rollup_answers") or {}) if isinstance(controlled.get("rollup_answers"), dict) else {}
    avenues.append(
        (
            "A",
            0.9 if not ra.get("is_avenue_a_supervised_live_ready") else 0.4,
            "supervised_confirmation_path" if not ra.get("is_avenue_a_supervised_live_ready") else "maintain_truth_freshness",
        )
    )
    avenues.append(("B", 0.35, "independent_kalshi_research_queue"))
    avenues.append(("C", 0.25, "independent_options_research_queue"))
    avenues.sort(key=lambda x: -x[1])

    gates: List[Tuple[str, float, str]] = []
    ga_n = len((controlled.get("gate_a") or {}).get("gate_a_blockers_deduped") or [])
    gb_n = len((controlled.get("gate_b") or {}).get("gate_b_blockers_deduped") or [])
    gates.append(("gate_a", 0.5 + min(ga_n, 8) * 0.05, f"blocker_count_{ga_n}"))
    gates.append(("gate_b", 0.55 + min(gb_n, 8) * 0.05, f"blocker_count_{gb_n}"))
    gates.sort(key=lambda x: -x[1])

    exp_queue: List[Dict[str, Any]] = []
    for e in exps[:40]:
        if not isinstance(e, dict):
            continue
        st = str(e.get("status") or "draft")
        if st in ("passed", "superseded"):
            continue
        exp_queue.append(
            {
                "experiment_id": e.get("experiment_id"),
                "priority": 0.6 if st == "running" else 0.45,
                "reason": f"status_{st}",
            }
        )
    exp_queue.sort(key=lambda x: -float(x.get("priority") or 0))

    blockers_raw: List[str] = []
    blockers_raw.extend(list(aut.get("active_blockers") or []))
    blockers_raw.extend((controlled.get("shared_infra_blockers_deduped") or []))
    scored_bl = sorted([(b, _score_blocker(str(b))) for b in blockers_raw], key=lambda x: -x[1])
    blocker_q = [{"blocker": b, "score": s} for b, s in scored_bl[:24]]

    highest_avenue = avenues[0][0] if avenues else "unknown"
    highest_gate = gates[0][0] if gates else "unknown"
    top_exp = exp_queue[0] if exp_queue else None
    top_blocker = blocker_q[0]["blocker"] if blocker_q else None

    snap = {
        "truth_version": "opportunity_pressure_snapshot_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "no_fake_forecast": True,
        "expected_value_of_attention": (
            "bounded_qualitative: focus on removing infrastructure_and_proof_blockers_before_scaling_exposure"
        ),
        "highest_priority_avenue": highest_avenue,
        "highest_priority_gate": highest_gate,
        "highest_priority_experiment": top_exp,
        "highest_priority_blocker_to_remove": top_blocker,
        "highest_priority_research_path": "evidence_first_replay_and_sim_before_live_variants",
        "highest_priority_operational_cleanup": "refresh_runtime_artifacts_and_recompute_controlled_live_readiness",
    }
    write_json_atomic(opportunity_pressure_snapshot_path(root), snap)

    aq = {
        "truth_version": "avenue_priority_queue_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "ranked": [{"avenue": a, "score": s, "note": n} for a, s, n in avenues],
    }
    gq = {
        "truth_version": "gate_priority_queue_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "ranked": [{"gate": g, "score": s, "note": n} for g, s, n in gates],
    }
    eq = {
        "truth_version": "experiment_priority_queue_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "ranked": exp_queue[:30],
    }
    bq = {
        "truth_version": "blocker_priority_queue_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "ranked": blocker_q,
    }
    write_json_atomic(avenue_priority_queue_path(root), aq)
    write_json_atomic(gate_priority_queue_path(root), gq)
    write_json_atomic(experiment_priority_queue_path(root), eq)
    write_json_atomic(blocker_priority_queue_path(root), bq)

    return {
        "opportunity_pressure_snapshot": snap,
        "avenue_priority_queue": aq,
        "gate_priority_queue": gq,
        "experiment_priority_queue": eq,
        "blocker_priority_queue": bq,
    }
