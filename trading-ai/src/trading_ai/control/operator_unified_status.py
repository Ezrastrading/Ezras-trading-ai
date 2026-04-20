"""Single machine-readable operator rollup — explicit sources, no silent merge."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.governance.storage_architecture import global_memory_dir
from trading_ai.intelligence.truth_contract import summarize_policies
from trading_ai.nte.paths import nte_memory_dir
from trading_ai.runtime_paths import ezras_runtime_root


def _read(p: Path) -> Optional[Dict[str, Any]]:
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def build_operator_unified_status(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Concise status: goals, EIE, Gate A/B hints, truth policies, data quality.

    Sources are labeled per field; discrepancies are passed through when present.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ts = datetime.now(timezone.utc).isoformat()
    gdir = global_memory_dir()
    nte = nte_memory_dir()

    ei = _read(gdir / "global_execution_intelligence_snapshot.json") or {}
    ei_nte = _read(nte / "execution_intelligence_snapshot.json") or {}
    disc = _read(nte / "execution_intelligence_discrepancy_report.json") or {}
    tss = _read(nte / "truth_source_summary.json") or {}
    gp = _read(nte / "goal_progress_snapshot.json") or {}
    gb_path = root / "data" / "reports" / "gate_b_operator_readiness_compact.json"

    autonomous = {}
    try:
        from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path

        autonomous = build_autonomous_operator_path(runtime_root=root)
    except Exception:
        autonomous = {"error": "autonomous_operator_path_unavailable"}

    gate_b = _read(gb_path) or {}
    ap_path = root / "data" / "control" / "operator_runtime_summary.json"

    ap_nte = ei_nte.get("bundle") if isinstance(ei_nte, dict) else {}
    prog = (ap_nte.get("progress") if isinstance(ap_nte, dict) else None) or gp.get("goal_progress") or {}

    out: Dict[str, Any] = {
        "truth_version": "operator_unified_status_v1",
        "generated_at": ts,
        "runtime_root": str(root),
        "truth_policies": summarize_policies(),
        "strongest_avenue_now": (ei.get("avenue_performance") or {}).get("strongest_avenue"),
        "weakest_avenue_now": (ei.get("avenue_performance") or {}).get("weakest_avenue"),
        "active_goal": prog.get("goal_id") or (ei.get("goals") or {}).get("goal_id"),
        "distance_to_goal": {
            "progress_pct": prog.get("progress_pct"),
            "current_position": prog.get("current_position"),
            "trajectory_status": prog.get("trajectory_status"),
        },
        "todays_best_steps": list((prog.get("recommended_next_steps_today") or [])[:12]),
        "tomorrows_best_steps": list((prog.get("recommended_next_steps_tomorrow") or [])[:12]),
        "autonomous_blocker_domain_groups_v2": (autonomous.get("operator_blocker_domain_groups_v2")),
        "autonomous_progression": autonomous.get("progression"),
        "gate_a_readiness_summary": {
            "note": "See avenue A closure bundle and supervised daemon truth — not collapsed here.",
            "refs": ["data/control/avenue_a_final_live_blockers.json"],
        },
        "gate_b_readiness_summary": gate_b,
        "data_quality_summary": (ap_nte.get("system_state") or {}).get("data_quality")
        if isinstance(ap_nte, dict)
        else ei.get("data_sufficiency"),
        "truth_source_summary": tss,
        "discrepancy_summary": disc,
        "sources": {
            "global_ei_snapshot": str(gdir / "global_execution_intelligence_snapshot.json"),
            "nte_ei_snapshot": str(nte / "execution_intelligence_snapshot.json"),
            "goal_progress_nte": str(nte / "goal_progress_snapshot.json"),
            "gate_b_operator_compact": str(gb_path),
        },
        "honesty": "This object aggregates pointers and summaries; authoritative halts and governance gates remain on their own artifacts.",
    }
    try:
        ap_path.parent.mkdir(parents=True, exist_ok=True)
        ap_path.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError:
        pass
    return out
