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


def write_daily_ceo_review(*, registry_path: Optional[Path] = None, estimated_review_tokens: int = 800) -> Dict[str, Any]:
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
    by_avenue: Dict[str, List[str]] = defaultdict(list)
    by_gate: Dict[str, List[str]] = defaultdict(list)
    by_class: Dict[str, List[str]] = defaultdict(list)
    strongest: List[Dict[str, Any]] = []
    weakest: List[Dict[str, Any]] = []
    promotion_histogram: Dict[str, int] = defaultdict(int)
    capital_histogram: Dict[str, int] = defaultdict(int)
    for b in bots:
        aid = str(b.get("avenue") or "?")
        g = str(b.get("gate") or "?")
        bc = str(b.get("bot_class") or "?")
        bid = str(b.get("bot_id") or "?")
        promotion_histogram[str(b.get("promotion_tier") or "?")] += 1
        capital_histogram[str(b.get("capital_authority_tier") or "?")] += 1
        by_avenue[aid].append(bid)
        by_gate[f"{aid}|{g}"].append(bid)
        by_class[bc].append(bid)
        rel = float((b.get("performance") or {}).get("composite", {}).get("trust_score") or b.get("reliability_score") or 0.0)
        strongest.append({"bot_id": bid, "reliability": rel})
    strongest.sort(key=lambda x: x["reliability"], reverse=True)
    weakest = sorted(strongest, key=lambda x: x["reliability"])[: min(5, len(strongest))]
    strongest = strongest[: min(5, len(strongest))]
    bud = load_budget_state()
    shadow_ids = [str(b.get("bot_id")) for b in bots if str(b.get("permission_level") or "") != PermissionLevel.EXECUTION_AUTHORITY.value]
    live_perm_ids = [str(b.get("bot_id")) for b in bots if str(b.get("permission_level") or "") == PermissionLevel.EXECUTION_AUTHORITY.value]
    objective_weights = {
        "upside_velocity_vs_truth": 0.35,
        "time_to_convergence": 0.25,
        "capital_efficiency": 0.15,
        "token_efficiency": 0.15,
        "live_safety_compliance": 0.1,
    }
    top_edge = (edge_snap.get("bots_ranked") or [])[:5]
    top_ttc = (ttc_snap.get("paths_ranked") or [])[:5]
    gap_detection = _coverage_gaps(bots) + (
        ["low_edge_diversity"] if len(top_edge) < 2 else []
    )
    decision_outputs = _decision_outputs(bots, top_edge, top_ttc, gap_detection)
    traj = _trajectory_note(bots, edge_snap)

    out = {
        "truth_version": "ceo_daily_orchestration_review_v2",
        "generated_at": _iso(),
        "system_mission": system_mission_dict(),
        "mission_prompt_injection": mission_prompt_injection_block(),
        "ceo_objective_weights": objective_weights,
        "profitability_acceleration_ranking": [x.get("bot_id") for x in top_edge],
        "convergence_acceleration_ranking": [x.get("bot_id") for x in top_ttc],
        "implementation_priority_ranking": [x.get("bot_id") for x in top_ttc[:8]],
        "edge_discovery_snapshot_ref": "data/governance/orchestration/edge_discovery_snapshot.json",
        "time_to_convergence_snapshot_ref": "data/governance/orchestration/time_to_convergence_snapshot.json",
        "global_performance_review": {
            "bots_ranked_by_edge": top_edge,
            "paths_ranked_by_time_to_usefulness": top_ttc,
        },
        "gap_detection": gap_detection,
        "decision_outputs": decision_outputs,
        "trajectory_vs_aggressive_upside_target": traj,
        "capital_readiness_notes": [
            "Live capital ramps require promotion contracts + execution authority slots — unchanged.",
        ],
        "live_safety_notes": [
            "No automatic live permission elevation from this review.",
            "Fail-closed if orchestration_truth_chain reports blockers.",
        ],
        "review_budget": {
            "allowed": allow_review,
            "reason": review_why,
            "per_ceo_review_token_budget": bud.get("per_ceo_review_token_budget"),
            "ceo_review_tokens_used_today": bud.get("ceo_review_tokens_used_today"),
        },
        "bot_total": len(bots),
        "summary_by_avenue": {k: {"count": len(v), "bot_ids": v} for k, v in sorted(by_avenue.items())},
        "summary_by_gate": {k: {"count": len(v), "bot_ids": v} for k, v in sorted(by_gate.items())},
        "summary_by_bot_class": {k: {"count": len(v), "bot_ids": v} for k, v in sorted(by_class.items())},
        "strongest_bots": strongest,
        "weakest_bots": weakest,
        "missing_coverage_notes": _coverage_gaps(bots),
        "duplicate_scope_risks": _dup_notes(bots),
        "cost_snapshot": {
            "global_daily_token_budget": bud.get("global_daily_token_budget"),
            "ai_calls_this_hour": bud.get("ai_calls_this_hour"),
        },
        "promotion_tier_histogram": dict(sorted(promotion_histogram.items())),
        "capital_authority_tier_histogram": dict(sorted(capital_histogram.items())),
        "recommendations": _recommendations(bots),
        "shadow_non_execution_bot_ids": shadow_ids,
        "execution_authority_permission_bot_ids": live_perm_ids,
        "system_risk_notes": _risk_notes(bots, bud),
        "honesty": "Deterministic summary grounded in registry + measured artifacts; aggressive upside is a scored trajectory — not a promised return. Does not grant live authority.",
    }
    try:
        rst = ReviewStorage()
        ei = rst.load_json("global_execution_intelligence_snapshot.json")
        gp = rst.load_json("goal_progress_snapshot.json")
        out["execution_intelligence_operator_brief"] = {
            "truth_version": ei.get("truth_version"),
            "strongest_avenue": (ei.get("avenue_performance") or {}).get("strongest_avenue")
            if isinstance(ei.get("avenue_performance"), dict)
            else None,
            "weakest_avenue": (ei.get("avenue_performance") or {}).get("weakest_avenue")
            if isinstance(ei.get("avenue_performance"), dict)
            else None,
            "recommended_capital_split": (ei.get("capital_allocation") or {}).get("allocation_map")
            if isinstance(ei.get("capital_allocation"), dict)
            else {},
            "scaling_posture": ei.get("scaling"),
            "strategy_promotions": (ei.get("strategy_state") or {}).get("promoted_ids")
            if isinstance(ei.get("strategy_state"), dict)
            else [],
            "strategy_restrictions": (ei.get("strategy_state") or {}).get("restricted_ids")
            if isinstance(ei.get("strategy_state"), dict)
            else [],
            "goal_progress": gp.get("goal_progress"),
            "best_steps_today": list(((gp.get("goal_progress") or {}).get("recommended_next_steps_today") or [])[:10]),
            "best_steps_tomorrow": list(((gp.get("goal_progress") or {}).get("recommended_next_steps_tomorrow") or [])[:10]),
            "biggest_blocker": list(((gp.get("goal_progress") or {}).get("blockers") or [])[:3]),
            "missing_evidence": (ei.get("data_sufficiency") or {}).get("notes")
            if isinstance(ei.get("data_sufficiency"), dict)
            else [],
            "advisory_only": True,
        }
    except Exception:
        pass
    if allow_review:
        record_ceo_review_tokens(estimated_review_tokens)
        try:
            impl_q = implementation_queue_path()
            n_impl = len(json.loads(impl_q.read_text(encoding="utf-8")).get("items") or [])
            if n_impl == 0 and len(bots) > 0:
                queue_implementation_item(
                    title="Refresh trade_cycle_intelligence + edge snapshots after next supervised cycle",
                    change_class="shadow_only",
                    evidence_refs=["edge_discovery_snapshot.json", "trade_cycle_intelligence.json"],
                    risk_notes="No live mutation",
                )
        except OSError:
            pass
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
            recs.append(f"consider_promotion_review:{b.get('bot_id')}")
        if b.get("demotion_risk"):
            recs.append(f"demotion_review:{b.get('bot_id')}")
    return recs[:32]


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
