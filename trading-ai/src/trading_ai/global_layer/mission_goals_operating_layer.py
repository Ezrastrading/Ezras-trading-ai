"""
Mission/Goals Operating Layer
=============================

Turns mission + goals into active, system-wide operating pressure:
- computes daily mission pace (required vs actual) and classifies pace state
- produces a concrete daily plan (review/research/test/implement) without overriding safety
- writes runtime artifacts and seeds orchestration queues used by bots/CEO review

This layer is *advisory* to execution (never bypasses kill switches / gate truth),
but it is *active* in orchestration: it influences what work gets prioritized next.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from trading_ai.global_layer.automation_queues import ensure_automation_queues_initialized
from trading_ai.global_layer.orchestration_paths import (
    experiment_queue_path,
    implementation_queue_path,
    research_queue_path,
    validation_queue_path,
)
from trading_ai.intelligence.execution_intelligence.goals import default_goal_order, get_goal
from trading_ai.runtime_paths import ezras_runtime_root


PaceState = Literal["behind_pace", "on_pace", "ahead_of_pace", "unknown"]


@dataclass(frozen=True)
class MissionPaceSnapshot:
    required_daily_pct: float
    actual_daily_pct: Optional[float]
    pace_state: PaceState
    ratio_actual_to_required: Optional[float]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_mission_pace(
    *,
    required_daily_pct: float,
    actual_daily_pct: Optional[float],
    on_band: float = 0.05,
) -> MissionPaceSnapshot:
    """
    Classify pace relative to mission required daily %.

    - behind_pace: actual < required*(1-on_band)
    - on_pace: within +/- on_band
    - ahead_of_pace: actual > required*(1+on_band)

    If actual is unknown, returns unknown.
    """
    req = float(required_daily_pct)
    act = None if actual_daily_pct is None else float(actual_daily_pct)
    if act is None or req <= 0:
        return MissionPaceSnapshot(required_daily_pct=req, actual_daily_pct=act, pace_state="unknown", ratio_actual_to_required=None)
    ratio = act / req if req else None
    lo = req * (1.0 - float(on_band))
    hi = req * (1.0 + float(on_band))
    if act < lo:
        state: PaceState = "behind_pace"
    elif act > hi:
        state = "ahead_of_pace"
    else:
        state = "on_pace"
    return MissionPaceSnapshot(required_daily_pct=req, actual_daily_pct=act, pace_state=state, ratio_actual_to_required=ratio)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _merge_queue_item(queue: Dict[str, Any], key: str, item: Dict[str, Any]) -> None:
    xs = queue.get(key)
    if not isinstance(xs, list):
        xs = []
    seen = set()
    out: List[Dict[str, Any]] = []
    for x in xs:
        if not isinstance(x, dict):
            continue
        iid = str(x.get("id") or "")
        if iid:
            seen.add(iid)
        out.append(x)
    iid2 = str(item.get("id") or "")
    if iid2 and iid2 not in seen:
        out.insert(0, item)  # newest first
    queue[key] = out
    queue["generated_at"] = _iso()


def _seed_queue_item(
    *,
    path: Path,
    truth_version: str,
    list_key: str,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    existing = _read_json(path) or {"truth_version": truth_version, "generated_at": _iso(), list_key: []}
    if not isinstance(existing, dict):
        existing = {"truth_version": truth_version, "generated_at": _iso(), list_key: []}
    _merge_queue_item(existing, list_key, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return existing


def _extract_actual_daily_from_million_tracker(runtime_root: Path) -> Optional[float]:
    """
    Best-effort actual daily growth estimate from Shark million tracker snapshots.
    Returns percent per day, or None if insufficient data.
    """
    try:
        from trading_ai.shark.million_tracker import MILLION_FILE

        p = Path(MILLION_FILE)
        if not p.is_file():
            return None
        blob = _read_json(p) or {}
        snaps = blob.get("snapshots") or []
        snaps = [s for s in snaps if isinstance(s, dict)]
        if len(snaps) < 2:
            return None
        # Use oldest/newest in last 7 days window when possible.
        import time as _time

        cutoff = _time.time() - 7 * 86400
        recent = [s for s in snaps if float(s.get("unix_ts") or 0) >= cutoff]
        use = recent if len(recent) >= 2 else snaps[-2:]
        a = float(use[0].get("total") or 0)
        b = float(use[-1].get("total") or 0)
        if a <= 0 or b <= 0:
            return None
        days = max(1.0, float(len(use) - 1))
        return ((b / a) ** (1.0 / days) - 1.0) * 100.0
    except Exception:
        return None


def compute_mission_pace_snapshot(*, total_balance_usd: float, runtime_root: Optional[Path] = None) -> MissionPaceSnapshot:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    required = 0.25
    try:
        from trading_ai.shark.mission import get_mission_status

        ms = get_mission_status(float(total_balance_usd))
        required = float(ms.get("required_daily_pct") or required)
    except Exception:
        raw = (os.environ.get("EZRAS_MISSION_REQUIRED_DAILY_PCT") or "").strip()
        if raw:
            try:
                required = float(raw)
            except ValueError:
                required = 0.25
    actual = _extract_actual_daily_from_million_tracker(root)
    return classify_mission_pace(required_daily_pct=required, actual_daily_pct=actual)


def build_daily_operating_plan(
    *,
    pace: MissionPaceSnapshot,
    active_goal_id: str,
    safety_blockers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Produce concrete next actions across review/research/testing/implementation.
    This never requests bypassing safety — it escalates *proof* and *quality* work when behind pace.
    """
    goal = get_goal(active_goal_id) or {"id": active_goal_id, "name": active_goal_id}
    blockers = list(safety_blockers or [])

    # Default action pool (safe, cross-system).
    review: List[str] = [
        "refresh truth artifacts and control dashboards",
        "compare gate-level fee-adjusted expectancy vs last 7d",
        "identify top 3 loss clusters and top 3 win clusters",
    ]
    research: List[str] = [
        "rank opportunities by liquidity stability and verified edge quality",
        "investigate top blocker root-causes (data freshness, slippage, spread widening)",
    ]
    testing: List[str] = [
        "run smoke suite for gating + execution policy invariants",
        "run harness/replay on top candidate strategy changes before live",
    ]
    implementation: List[str] = [
        "queue smallest-surface fix for highest-impact blocker",
        "tighten instrumentation where decisions lack measurable attribution",
    ]

    if pace.pace_state == "behind_pace":
        # When behind: bias toward *closing blockers and improving validated throughput*, not reckless sizing.
        research.insert(0, "prioritize research that reduces false positives / bad fills (safety + expectancy)")
        testing.insert(0, "increase validation cadence for anything that affects entry/exit quality")
        implementation.insert(0, "implement top 1–2 fixes that increase safe trade throughput (not size increases)")
    elif pace.pace_state == "ahead_of_pace":
        # When ahead: bias toward stability and scalability.
        review.insert(0, "lock in what worked: snapshot configs, write rollback-ready artifacts")
        testing.insert(0, "stress-test edge stability under slippage/latency perturbations")

    if blockers:
        implementation.insert(0, f"resolve safety blockers first: {', '.join(blockers[:5])}")
        testing.insert(0, "verify kill-switch + fail-closed behaviors remain intact after changes")

    return {
        "truth_version": "mission_goals_operating_plan_v1",
        "generated_at_utc": _iso(),
        "pace": {
            "required_daily_pct": pace.required_daily_pct,
            "actual_daily_pct": pace.actual_daily_pct,
            "pace_state": pace.pace_state,
            "ratio_actual_to_required": pace.ratio_actual_to_required,
        },
        "active_goal": {"id": goal.get("id"), "name": goal.get("name")},
        "daily_loop": {
            "review": review[:12],
            "research": research[:12],
            "testing": testing[:12],
            "implementation": implementation[:12],
        },
        "safety_invariants": [
            "mission pressure never overrides kill switches, hard stops, or gate truth",
            "no forced profit targets; only prioritization and measured improvement",
            "fail-closed stays authoritative at every gate and venue",
        ],
    }


def refresh_mission_goals_operating_layer(
    *,
    total_balance_usd: float,
    runtime_root: Optional[Path] = None,
    active_goal_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    System-wide entrypoint for daily cycle hooks and smoke:
    - compute mission pace snapshot
    - build operating plan
    - write runtime artifact
    - seed orchestration queues with concrete next actions (cross-bot/gate/avenue)
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ensure_automation_queues_initialized()

    gid = str(active_goal_id or (default_goal_order()[0] if default_goal_order() else "GOAL_A"))
    pace = compute_mission_pace_snapshot(total_balance_usd=float(total_balance_usd), runtime_root=root)
    plan = build_daily_operating_plan(pace=pace, active_goal_id=gid)

    # Runtime artifact (per-host run, per day).
    art = root / "data" / "control" / "mission_goals_operating_plan.json"
    _write_json_atomic(art, plan)
    (root / "data" / "control" / "mission_goals_operating_plan.txt").write_text(
        json.dumps(plan, indent=2, default=str)[:24000] + "\n",
        encoding="utf-8",
    )

    # Seed orchestration queues with the top actions so bots and CEO review are automatically guided.
    def _mk_items(kind: str, actions: Iterable[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i, a in enumerate(list(actions)[:6]):
            out.append(
                {
                    "id": f"mission_goals::{kind}::{datetime.now(timezone.utc).date().isoformat()}::{i}",
                    "created_at": _iso(),
                    "source": "mission_goals_operating_layer",
                    "kind": kind,
                    "action": str(a),
                    "pace_state": pace.pace_state,
                    "active_goal_id": gid,
                    "safety_note": "does_not_override_gates_or_kill_switches",
                }
            )
        return out

    loop = plan.get("daily_loop") or {}
    for it in _mk_items("research", loop.get("research") or []):
        _seed_queue_item(path=research_queue_path(), truth_version="research_queue_v1", list_key="entries", item=it)
    for it in _mk_items("experiment", loop.get("testing") or []):
        _seed_queue_item(path=experiment_queue_path(), truth_version="experiment_queue_v1", list_key="experiments", item=it)
    for it in _mk_items("implementation", loop.get("implementation") or []):
        _seed_queue_item(path=implementation_queue_path(), truth_version="implementation_queue_v1", list_key="items", item=it)
    for it in _mk_items("validation", loop.get("review") or []):
        _seed_queue_item(path=validation_queue_path(), truth_version="validation_queue_v1", list_key="validations", item=it)

    return {"pace": pace, "plan": plan, "runtime_artifact": str(art)}

