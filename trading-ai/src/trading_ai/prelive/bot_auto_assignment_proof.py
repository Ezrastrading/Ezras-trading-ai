"""Prove registry-driven bot auto-assignment for synthetic avenue/gates."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.global_layer.bot_hierarchy.registry import list_bots
from trading_ai.multi_avenue.avenue_factory import register_avenue
from trading_ai.multi_avenue.gate_factory import register_gate
from trading_ai.multi_avenue.scoped_paths import system_control_dir
from trading_ai.prelive._io import write_control_json, write_control_txt


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(*, runtime_root: Path) -> Dict[str, Any]:
    """
    Proof requirements:
    - New avenue registered → avenue master + default avenue roles exist.
    - New gate registered → gate manager + default gate roles exist.
    - No bespoke per-gate code required (config/registry only).
    """
    root = Path(runtime_root).resolve()

    # Keep bot hierarchy artifacts scoped to this runtime proof run.
    hroot = system_control_dir(runtime_root=root) / "bot_hierarchy"
    hroot.mkdir(parents=True, exist_ok=True)
    os.environ["EZRAS_BOT_HIERARCHY_ROOT"] = str(hroot)

    register_avenue(
        {
            "avenue_id": "Z",
            "avenue_name": "synthetic_avenue_z",
            "display_name": "Synthetic Avenue Z",
            "venue_name": "mock",
            "market_type": "mock",
            "wiring_status": "scaffold_only",
            "notes": "bot_auto_assignment_proof",
            "gates": [],
        },
        runtime_root=root,
    )
    register_gate("Z", "gate_z1", runtime_root=root)

    bots = [b.model_dump(mode="json") for b in list_bots(path=hroot)]

    def _has(*, bot_type: str, avenue_id: str, gate_id: str | None, bot_role: str | None) -> bool:
        for b in bots:
            if str(b.get("bot_type")) != bot_type:
                continue
            if str(b.get("avenue_id")) != avenue_id:
                continue
            if (b.get("gate_id") or None) != gate_id:
                continue
            if (b.get("bot_role") or None) != bot_role:
                continue
            return True
        return False

    required: List[Dict[str, Any]] = [
        {"bot_type": "ezra_governor", "avenue_id": "system", "gate_id": None, "bot_role": None},
        {"bot_type": "avenue_master", "avenue_id": "Z", "gate_id": None, "bot_role": "avenue_master"},
        {"bot_type": "avenue_worker", "avenue_id": "Z", "gate_id": None, "bot_role": "research_bot"},
        {"bot_type": "avenue_worker", "avenue_id": "Z", "gate_id": None, "bot_role": "opportunity_ranking_bot"},
        {"bot_type": "avenue_worker", "avenue_id": "Z", "gate_id": None, "bot_role": "capital_allocation_bot"},
        {"bot_type": "avenue_worker", "avenue_id": "Z", "gate_id": None, "bot_role": "alerting_bot"},
        {"bot_type": "avenue_worker", "avenue_id": "Z", "gate_id": None, "bot_role": "review_bot"},
        {"bot_type": "gate_manager", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "gate_manager"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "scanner_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "strategy_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "risk_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "execution_validation_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "exit_manager_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "rebuy_manager_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "rebuy_decision_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "profit_progression_bot"},
        {"bot_type": "gate_worker", "avenue_id": "Z", "gate_id": "gate_z1", "bot_role": "goal_progression_bot"},
    ]

    missing = [r for r in required if not _has(**r)]
    ok = len(missing) == 0

    payload: Dict[str, Any] = {
        "artifact": "bot_auto_assignment_proof",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "hierarchy_root": str(hroot),
        "synthetic": {"avenue_id": "Z", "gate_id": "gate_z1"},
        "ok": ok,
        "missing": missing,
        "bot_count": len(bots),
        "honesty": "Proof checks bot materialization only; it does not imply execution wiring for synthetic scopes.",
    }

    write_control_json("bot_auto_assignment_proof.json", payload, runtime_root=root)
    write_control_txt("bot_auto_assignment_proof.txt", json.dumps(payload, indent=2) + "\n", runtime_root=root)
    return payload

