"""Canonical automation queues (JSON, merge-safe) — research, experiments, validation, rapid upside."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.global_layer.orchestration_paths import (
    blocked_opportunities_path,
    experiment_queue_path,
    implementation_queue_path,
    rapid_upside_queue_path,
    research_queue_path,
    validation_queue_path,
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_if_missing(path: Path, payload: Dict[str, Any]) -> bool:
    if path.is_file():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def ensure_automation_queues_initialized() -> Dict[str, bool]:
    """Idempotent: seed empty queue files for bots / CEO."""
    created: Dict[str, bool] = {}
    specs = [
        (research_queue_path(), "research_queue_v1", "entries"),
        (experiment_queue_path(), "experiment_queue_v1", "experiments"),
        (implementation_queue_path(), "implementation_queue_v1", "items"),
        (validation_queue_path(), "validation_queue_v1", "validations"),
        (rapid_upside_queue_path(), "rapid_upside_opportunities_queue_v1", "candidates"),
        (blocked_opportunities_path(), "blocked_opportunity_reasons_v1", "blockers"),
    ]
    for path, ver, key in specs:
        created[str(path.name)] = _write_if_missing(
            path,
            {
                "truth_version": ver,
                "generated_at": _iso(),
                key: [],
                "honesty": "Queues are evidence-backed work items — empty default.",
            },
        )
    return created
