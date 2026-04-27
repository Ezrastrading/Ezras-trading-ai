"""Canonical daily CEO review artifact — aggregates all bots (deterministic summary).

Tracked path: ``src/trading_ai/global_layer/ceo_daily_orchestration.py`` — write output via
:func:`write_daily_ceo_review` to the path from :mod:`trading_ai.global_layer.orchestration_paths`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.automation_queues import ensure_automation_queues_initialized
from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.budget_governor import can_run_ceo_review, load_budget_state, record_ceo_review_tokens
from trading_ai.global_layer.edge_discovery_engine import build_edge_discovery_snapshot
from trading_ai.global_layer.implementation_governor import ensure_implementation_governor_state, queue_implementation_item
from trading_ai.global_layer.orchestration_paths import ceo_daily_review_path, implementation_queue_path
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.orchestration_schema import PermissionLevel
from trading_ai.global_layer.system_mission import mission_prompt_injection_block, system_mission_dict
from trading_ai.global_layer.time_to_convergence_engine import build_time_to_convergence_snapshot


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_daily_ceo_review(*, registry_path: Optional[Path] = None, estimated_review_tokens: int = 400) -> Dict[str, Any]:
    ensure_automation_queues_initialized()
    allow_review, review_why = can_run_ceo_review(estimated_tokens=estimated_review_tokens)
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    runtime_root: Optional[Path] = None
    try:
        from trading_ai.runtime_paths import ezras_runtime_root
        runtime_root = Path(ezras_runtime_root()).resolve()
    except Exception:
        runtime_root = None

    edge_snap = build_edge_discovery_snapshot(registry_path=registry_path, runtime_root=runtime_root)
    ttc_snap = build_time_to_convergence_snapshot(registry_path=registry_path)
    ensure_implementation_governor_state()
    
    strongest: List[Dict[str, Any]] = []
    for b in bots:
        bid = str(b.get("bot_id") or "?")
        rel = float((b.get("performance") or {}).get("composite", {}).get("trust_score") or b.get("reliability_score") or 0.0)
        strongest.append({"bot_id": bid, "reliability": rel})
    strongest.sort(key=lambda x: x["reliability"], reverse=True)
    strongest = strongest[:3]  # Limit to top 3
    weakest = sorted(strongest, key=lambda x: x["reliability"])[:2]  # Limit to bottom 2
    
    bud = load_budget_state()
    shadow_ids = [str(b.get("bot_id")) for b in bots if str(b.get("permission_level") or "") != PermissionLevel.EXECUTION_AUTHORITY.value]
    live_perm_ids = [str(b.get("bot_id")) for b in bots if str(b.get("permission_level") or "") == PermissionLevel.EXECUTION_AUTHORITY.value]
    
    top_edge = (edge_snap.get("bots_ranked") or [])[:3]  # Limit to top 3
    top_ttc = (ttc_snap.get("paths_ranked") or [])[:3]  # Limit to top 3
    gap_detection = _coverage_gaps(bots)[:2]  # Limit to 2 gaps
    decision_outputs = _decision_outputs(bots, top_edge, top_ttc, gap_detection)
    traj = _trajectory_note(bots, edge_snap)

    out = {
        "truth_version": "ceo_daily_orchestration_review_v3",
        "generated_at": _iso(),
        "profitability_acceleration_ranking": [x.get("bot_id") for x in top_edge],
        "convergence_acceleration_ranking": [x.get("bot_id") for x in top_ttc],
        "gap_detection": gap_detection,
        "decision_outputs": decision_outputs,
        "trajectory_vs_aggressive_upside_target": traj,
        "review_budget": {
            "allowed": allow_review,
            "reason": review_why,
            "per_ceo_review_token_budget": bud.get("per_ceo_review_token_budget"),
            "ceo_review_tokens_used_today": bud.get("ceo_review_tokens_used_today"),
        },
        "bot_total": len(bots),
        "strongest_bots": strongest,
        "weakest_bots": weakest,
        "recommendations": _recommendations(bots)[:5],  # Limit to 5
        "shadow_non_execution_bot_ids": shadow_ids,
        "execution_authority_permission_bot_ids": live_perm_ids,
        "system_risk_notes": _risk_notes(bots, bud),
        "honesty": "Deterministic summary; does not grant live authority.",
    }
    
    try:
        if runtime_root:
            from trading_ai.safety.kill_switch_engine import ceo_kill_switch_dashboard
            out["kill_switch_ceo_snapshot"] = ceo_kill_switch_dashboard(runtime_root=runtime_root, max_events=10)  # Limit to 10 events
    except Exception:
        pass
    
    try:
        rst = ReviewStorage()
        ei = rst.load_json("global_execution_intelligence_snapshot.json")
        gp = rst.load_json("goal_progress_snapshot.json")
        out["execution_intelligence_operator_brief"] = {
            "strongest_avenue": (ei.get("avenue_performance") or {}).get("strongest_avenue")
            if isinstance(ei.get("avenue_performance"), dict) else None,
            "weakest_avenue": (ei.get("avenue_performance") or {}).get("weakest_avenue")
            if isinstance(ei.get("avenue_performance"), dict) else None,
            "strategy_promotions": (ei.get("strategy_state") or {}).get("promoted_ids")[:3] if isinstance(ei.get("strategy_state"), dict) else [],
            "goal_progress": gp.get("goal_progress"),
            "biggest_blocker": list(((gp.get("goal_progress") or {}).get("blockers") or [])[:1]),  # Limit to 1
            "advisory_only": True,
        }
    except Exception:
        pass
    
    if allow_review:
        record_ceo_review_tokens(estimated_review_tokens)
    
    p = ceo_daily_review_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _risk_notes(bots: List[Dict[str, Any]], bud: Dict[str, Any]) -> List[str]:
    notes = []
    stale = [b for b in bots if str(b.get("status") or "") == "stale"]
    if stale:
        notes.append(f"stale_bots_count={len(stale)}")
    if bool(bud.get("review_budget_exhausted")):
        notes.append("ceo_review_budget_exhausted")
    glob_used = int(bud.get("global_token_used") or 0)
    glob_cap = int(bud.get("global_daily_token_budget") or 0)
    if glob_cap and glob_used >= glob_cap * 0.9:
        notes.append("global_token_budget_pressure")
    return notes


def _coverage_gaps(bots: List[Dict[str, Any]]) -> List[str]:
    avenues = {str(b.get("avenue")) for b in bots}
    gaps = []
    for need in ("A", "B", "C"):
        if need not in avenues:
            gaps.append(f"no_bots_for_avenue_{need}")
    return gaps


def _dup_notes(bots: List[Dict[str, Any]]) -> List[str]:
    seen: Dict[str, int] = {}
    notes = []
    for b in bots:
        dg = str(b.get("duplicate_guard_key") or "")
        seen[dg] = seen.get(dg, 0) + 1
    for k, v in seen.items():
        if v > 1:
            notes.append(f"duplicate_guard_collision:{k}:{v}")
    return notes


def _recommendations(bots: List[Dict[str, Any]]) -> List[str]:
    recs = []
    for b in bots:
        if str(b.get("permission_level")) == PermissionLevel.OBSERVE_ONLY.value and float(b.get("reliability_score") or 0) > 0.8:
            recs.append(f"promote:{b.get('bot_id')}")
        if b.get("demotion_risk"):
            recs.append(f"demote:{b.get('bot_id')}")
    return recs[:10]  # Limit to 10


def _decision_outputs(
    bots: List[Dict[str, Any]],
    top_edge: List[Dict[str, Any]],
    top_ttc: List[Dict[str, Any]],
    gaps: List[str],
) -> Dict[str, Any]:
    scale = [x.get("bot_id") for x in top_edge[:2] if (x.get("edge_score") or 0) >= 0.55]
    reduce = [x.get("bot_id") for x in (top_edge or [])[-2:] if x and (x.get("edge_score") or 0) < 0.25]
    spawn = ["canonical_specialists_if_missing_scope"] if gaps else []
    disable = [b.get("bot_id") for b in bots if b.get("demotion_risk")]
    return {
        "scale_strategies_or_bots": scale,
        "reduce_or_demote": reduce,
        "spawn_bots": spawn,
        "disable_bots": disable,
        "parameter_changes": ["none_automatic_without_truth_contract"],
    }


def _trajectory_note(bots: List[Dict[str, Any]], edge_snap: Dict[str, Any]) -> Dict[str, Any]:
    br = edge_snap.get("bots_ranked") or []
    best = (br[0].get("edge_score") if br else None) or 0.0
    return {
        "best_measured_edge_score_in_registry": best,
        "estimated_convergence_pressure": "increase_trade_and_replay_evidence_density",
        "honesty": "No calendar ETA; trajectory tightens only with additional measured outcomes.",
    }
