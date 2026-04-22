from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_plan(tmp_path: Path, *, pace_state: str, goal_id: str) -> None:
    p = tmp_path / "data" / "control" / "mission_goals_operating_plan.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "truth_version": "mission_goals_operating_plan_v2",
                "pace": {"pace_state": pace_state, "required_daily_pct": 5.0, "actual_daily_pct": 3.0},
                "active_goal": {"id": goal_id, "name": goal_id},
                "daily_loop": {"review": ["r"], "research": ["re"], "testing": ["t"], "implementation": ["i"]},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_queue(path: Path, key: str, kind: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"truth_version": f"{kind}_queue_v1", "generated_at": "x", key: [{"id": f"{kind}1", "action": f"{kind}_a"}]}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _write_registry(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal normalized-ish bot rows for scope enumeration.
    reg = {
        "truth_version": "bot_registry_v2",
        "updated_at": None,
        "bots": [
            {"bot_id": "b1", "role": "LEARNING", "avenue": "A", "gate": "gate_a", "lifecycle_state": "active"},
            {"bot_id": "b2", "role": "RISK", "avenue": "A", "gate": "gate_b", "lifecycle_state": "active"},
            {"bot_id": "b3", "role": "DECISION", "avenue": "B", "gate": "gate_a", "lifecycle_state": "shadow"},
        ],
    }
    path.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


def test_consumer_changes_task_priorities_by_pace_and_goal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    # Redirect tasks store to temp (avoid repo writes).
    tasks_store = tmp_path / "tasks.jsonl"
    monkeypatch.setattr("trading_ai.global_layer.task_registry.tasks_store_path", lambda: tasks_store)

    from trading_ai.global_layer.mission_goals_task_consumer import consume_mission_goals_into_tasks
    from trading_ai.global_layer.orchestration_paths import (
        experiment_queue_path,
        implementation_queue_path,
        research_queue_path,
        validation_queue_path,
    )

    reg_path = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(reg_path))
    _write_registry(reg_path)

    # Seed queue files (these are the real ones used by the operating layer).
    _write_queue(research_queue_path(), "entries", "research")
    _write_queue(experiment_queue_path(), "experiments", "experiment")
    _write_queue(implementation_queue_path(), "items", "implementation")
    _write_queue(validation_queue_path(), "validations", "validation")

    # Behind pace + GOAL_A should favor validation/experiments over implementation.
    _write_plan(tmp_path, pace_state="behind_pace", goal_id="GOAL_A")
    out_behind = consume_mission_goals_into_tasks(runtime_root=tmp_path, registry_path=reg_path, max_items_per_kind=1)
    assert out_behind["tasks_created"] > 0
    top_kind_behind = (out_behind["top_tasks"][0].get("mission_goals") or {}).get("kind")

    # Ahead pace + later goal should shift toward implementation relative priority.
    _write_plan(tmp_path, pace_state="ahead_of_pace", goal_id="GOAL_B")
    out_ahead = consume_mission_goals_into_tasks(runtime_root=tmp_path, registry_path=reg_path, max_items_per_kind=1)
    top_kind_ahead = (out_ahead["top_tasks"][0].get("mission_goals") or {}).get("kind")

    assert top_kind_behind != top_kind_ahead
    # Prove consumption reaches avenue+gate scopes.
    assert ("A", "gate_a") in out_behind["scopes_routed"]
    assert ("A", "gate_b") in out_behind["scopes_routed"]
    assert ("B", "gate_a") in out_behind["scopes_routed"]

