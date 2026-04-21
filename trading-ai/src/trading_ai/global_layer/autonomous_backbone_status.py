"""
Machine-readable backbone status — mission, automation flags, profitability paths, blockers.

Writes ``autonomous_backbone_status.json`` and can be embedded in CEO / truth chain.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.automation_queues import ensure_automation_queues_initialized
from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.budget_governor import load_budget_state
from trading_ai.global_layer.edge_discovery_engine import build_edge_discovery_snapshot
from trading_ai.global_layer.orchestration_detection import detect_bot_registry_anomalies
from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch
from trading_ai.global_layer.orchestration_paths import autonomous_backbone_status_path
from trading_ai.global_layer.orchestration_truth_chain import build_orchestration_truth_chain
from trading_ai.global_layer.orchestration_schema import PermissionLevel
from trading_ai.global_layer.system_mission import MISSION_VERSION, system_mission_dict
from trading_ai.global_layer.time_to_convergence_engine import build_time_to_convergence_snapshot


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def build_autonomous_backbone_status(
    *,
    registry_path: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
    write_file: bool = True,
) -> Dict[str, Any]:
    ensure_automation_queues_initialized()
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    chain = build_orchestration_truth_chain(registry_path=registry_path)
    bud = load_budget_state()
    ks = load_kill_switch()

    edge = build_edge_discovery_snapshot(registry_path=registry_path, runtime_root=runtime_root)
    ttc = build_time_to_convergence_snapshot(registry_path=registry_path)

    br = list(edge.get("bots_ranked") or [])
    top_prof = [x.get("bot_id") for x in br[:3]]
    worst_prof = [x.get("bot_id") for x in br[-3:]] if br else []
    top_conv = [x.get("bot_id") for x in (ttc.get("paths_ranked") or [])[:3]]

    def _useful(b: Dict[str, Any]) -> float:
        return float(
            (b.get("research_usefulness_score") or 0)
            or (b.get("profitability_score") or 0)
            or (b.get("reliability_score") or 0)
        )

    best_bots = sorted(bots, key=_useful, reverse=True)[:5]
    worst_bots = sorted(bots, key=_useful)[:5]

    live_green = len(chain.get("blockers") or []) == 0
    supervised_ready = bool((chain.get("readiness") or {}).get("supervised_live_operation"))
    autonomous_ready = bool((chain.get("readiness") or {}).get("autonomous_operation"))

    payload: Dict[str, Any] = {
        "truth_version": "autonomous_backbone_status_v1",
        "system_mission_version": MISSION_VERSION,
        "system_profitability_mission_active": True,
        "system_convergence_mission_active": True,
        "system_mission": system_mission_dict(),
        "mission_goals_operating_layer": {
            "active": True,
            "runtime_artifact_path": (
                str(Path(runtime_root).resolve() / "data" / "control" / "mission_goals_operating_plan.json")
                if runtime_root
                else None
            ),
            "honesty": "Advisory to execution, but used to seed research/test/implementation queues daily.",
        },
        "automatic_research_enabled": not bool(bud.get("review_budget_exhausted")),
        "automatic_spawn_enabled": not ks.get("orchestration_frozen"),
        "automatic_review_enabled": True,
        "automatic_ceo_enabled": True,
        "automatic_progression_enabled": True,
        "live_authority_green": live_green,
        "supervised_live_ready": supervised_ready,
        "supervised_daemon_ready": supervised_ready,
        "autonomous_ready": autonomous_ready,
        "orchestration_ready": not ks.get("orchestration_frozen") and live_green,
        "orchestration_live_gate_env": _env_flag("EZRAS_ORCHESTRATION_LIVE_GATE"),
        "top_current_profitability_paths": top_prof,
        "top_current_convergence_paths": top_conv,
        "most_wasteful_paths": worst_prof,
        "best_bots_by_usefulness": [b.get("bot_id") for b in best_bots],
        "best_bots_by_profitability_signal": [b.get("bot_id") for b in sorted(bots, key=lambda x: float(x.get("profitability_score") or 0), reverse=True)[:5]],
        "stale_bots": [b.get("bot_id") for b in bots if str(b.get("status") or "") == "stale"],
        "conflicting_bots": [],
        "registry_anomalies": detect_bot_registry_anomalies(registry_path=registry_path),
        "next_best_actions": list(chain.get("next_operator_commands") or [])[:12],
        "exact_blockers": list(chain.get("blockers") or []),
        "token_budget_pressure": bool(
            int(bud.get("global_token_used") or 0) >= int(bud.get("global_daily_token_budget") or 1) * 0.85
        ),
        "shadow_execution_bot_count": len([b for b in bots if str(b.get("permission_level")) != PermissionLevel.EXECUTION_AUTHORITY.value]),
        "honesty": "Flags reflect policy/env + truth chain — not a promise of profitability.",
    }
    if write_file:
        p = autonomous_backbone_status_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
