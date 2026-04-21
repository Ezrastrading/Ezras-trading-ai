"""A/B and shadow experiments — structured state machine."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer._bot_paths import global_layer_governance_dir
from trading_ai.global_layer.bot_types import ExperimentState


def experiments_path() -> Path:
    return global_layer_governance_dir() / "experiments.json"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_experiments() -> Dict[str, Any]:
    p = experiments_path()
    if not p.is_file():
        return {"truth_version": "experiments_v1", "items": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_experiments(data: Dict[str, Any]) -> None:
    p = experiments_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _iso()
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def register_experiment(
    *,
    name: str,
    bot_a: str,
    bot_b: str,
    mode: str = "shadow",
) -> Dict[str, Any]:
    data = load_experiments()
    items: List[Dict[str, Any]] = list(data.get("items") or [])
    exp = {
        "experiment_id": f"exp_{uuid.uuid4().hex[:12]}",
        "name": name,
        "bot_a": bot_a,
        "bot_b": bot_b,
        "mode": mode,
        "state": ExperimentState.PROPOSED.value,
        "created_at": _iso(),
    }
    items.append(exp)
    data["items"] = items
    save_experiments(data)
    return exp


def set_experiment_state(experiment_id: str, state: str) -> Dict[str, Any]:
    data = load_experiments()
    items = []
    found = None
    for it in data.get("items") or []:
        it = dict(it)
        if str(it.get("experiment_id")) == experiment_id:
            it["state"] = state
            it["state_at"] = _iso()
            found = it
        items.append(it)
    if not found:
        raise ValueError("experiment_not_found")
    data["items"] = items
    save_experiments(data)
    return found
