"""
Consume mission/goals operating outputs into real orchestration tasks.

Mission/goals is not "just reporting": it must actively influence what bots/gates/avenues pick up
next. This module is the smallest consumer that converts the operating plan + seeded queues into
task assignments (shadow routing only; no venue orders).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.bot_types import BotRole
from trading_ai.global_layer.orchestration_paths import (
    experiment_queue_path,
    implementation_queue_path,
    research_queue_path,
    validation_queue_path,
)
from trading_ai.global_layer.task_router import route_task_shadow
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _iter_scopes_from_registry(registry_path: Optional[Path] = None) -> List[Tuple[str, str]]:
    """
    Return unique (avenue, gate) scopes for routing.
    If none exist, return a conservative default.
    """
    reg = load_registry(registry_path)
    scopes: List[Tuple[str, str]] = []
    seen = set()
    for b in reg.get("bots") or []:
        if not isinstance(b, dict):
            continue
        av = str(b.get("avenue") or "").strip() or "A"
        gate = str(b.get("gate") or "").strip() or "none"
        key = (av, gate)
        if key in seen:
            continue
        seen.add(key)
        scopes.append(key)
    return scopes or [("A", "none")]


def _priority_boost(*, pace_state: str, active_goal_id: str, kind: str) -> int:
    """
    Deterministic priority boost.

    - behind pace: bias toward testing + implementation throughput fixes
    - ahead pace: bias toward validation/review stability work
    - goal influences: GOAL_A (bootstrapping) biases toward validation/testing; later goals bias toward implementation
    """
    ps = str(pace_state or "unknown")
    gid = str(active_goal_id or "GOAL_A")
    k = str(kind or "")
    boost = 0
    if ps == "behind_pace":
        boost += {"implementation": 120, "experiment": 100, "research": 70, "validation": 60}.get(k, 0)
    elif ps == "ahead_of_pace":
        boost += {"validation": 110, "experiment": 90, "research": 60, "implementation": 40}.get(k, 0)
    elif ps == "on_pace":
        boost += {"validation": 90, "experiment": 80, "research": 60, "implementation": 60}.get(k, 0)
    else:
        boost += {"validation": 60, "experiment": 60, "research": 50, "implementation": 50}.get(k, 0)

    if gid == "GOAL_A":
        boost += {"validation": 40, "experiment": 35, "research": 10, "implementation": 0}.get(k, 0)
    else:
        boost += {"implementation": 35, "research": 15, "experiment": 10, "validation": 10}.get(k, 0)
    return int(boost)


def _queue_items(kind: str) -> List[Dict[str, Any]]:
    if kind == "research":
        data = _read_json(research_queue_path())
        xs = data.get("entries") or []
    elif kind == "experiment":
        data = _read_json(experiment_queue_path())
        xs = data.get("experiments") or []
    elif kind == "implementation":
        data = _read_json(implementation_queue_path())
        xs = data.get("items") or []
    elif kind == "validation":
        data = _read_json(validation_queue_path())
        xs = data.get("validations") or []
    else:
        xs = []
    return [x for x in xs if isinstance(x, dict)]


def _role_for_kind(kind: str) -> str:
    if kind in ("research",):
        return BotRole.LEARNING.value
    if kind in ("experiment", "validation"):
        return BotRole.RISK.value
    return BotRole.DECISION.value


def consume_mission_goals_into_tasks(
    *,
    runtime_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    max_items_per_kind: int = 3,
    source_bot_id: str = "mission_goals_operating_layer",
) -> Dict[str, Any]:
    """
    Convert mission-goals operating outputs into real task assignments.

    This is intentionally shadow-only: it influences what gets worked on next, but never changes
    venue execution permissions or sizing directly.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    plan_path = root / "data" / "control" / "mission_goals_operating_plan.json"
    plan = _read_json(plan_path)
    pace = (plan.get("pace") or {}) if isinstance(plan.get("pace"), dict) else {}
    ps = str(pace.get("pace_state") or "unknown")
    ag = (plan.get("active_goal") or {}) if isinstance(plan.get("active_goal"), dict) else {}
    gid = str(ag.get("id") or "GOAL_A")

    scopes = _iter_scopes_from_registry(registry_path)
    created: List[Dict[str, Any]] = []

    for kind in ("validation", "research", "experiment", "implementation"):
        role = _role_for_kind(kind)
        items = _queue_items(kind)[: max(0, int(max_items_per_kind))]
        for it in items:
            tid = str(it.get("id") or "")
            action = str(it.get("action") or it.get("title") or kind)
            evidence = tid or f"mission_goals::{kind}::{_iso()}"
            prio = _priority_boost(pace_state=ps, active_goal_id=gid, kind=kind)

            # Route to each (avenue, gate) scope. This is the smallest “real consumption” that reaches bots/gates/avenues.
            for av, gate in scopes:
                row = route_task_shadow(
                    avenue=str(av),
                    gate=str(gate),
                    task_type=f"mission_goals::{kind}",
                    source_bot_id=source_bot_id,
                    role=role,
                    evidence_ref=evidence,
                )
                row["priority"] = int(row.get("priority") or 0) + prio
                row["mission_goals"] = {
                    "pace_state": ps,
                    "active_goal_id": gid,
                    "kind": kind,
                    "action": action,
                    "source_queue_item_id": tid,
                    "consumed_from": "mission_goals_operating_layer_queues",
                }
                created.append(row)

    # Deterministic ordering for callers that print proofs.
    created_sorted = sorted(
        created,
        key=lambda r: (
            -int(r.get("priority") or 0),
            str((r.get("mission_goals") or {}).get("active_goal_id") or ""),
        ),
    )
    return {
        "truth_version": "mission_goals_task_consumer_v1",
        "generated_at": _iso(),
        "plan_path": str(plan_path),
        "pace_state": ps,
        "active_goal_id": gid,
        "scopes_routed": scopes,
        "tasks_created": len(created_sorted),
        "top_tasks": created_sorted[:10],
        "honesty": "Shadow task routing only; does not grant live execution or bypass safety.",
    }

