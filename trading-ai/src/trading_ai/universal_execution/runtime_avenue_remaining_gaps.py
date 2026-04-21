"""
Structured remaining gaps — universal + per-avenue; honest about what blocks live vs advisory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _read(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def build_universal_remaining_gaps(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    gate_b_gaps = _read(ctrl / "gate_b_remaining_gaps_final.json") or {}
    lessons = _read(ctrl / "lessons_runtime_truth.json") or {}

    universal: List[Dict[str, Any]] = [
        {
            "id": "universal_orchestrator_not_fully_wired_to_all_venues",
            "classification": "universal",
            "blocks_live_orders_now": False,
            "blocks_repeated_ticks": False,
            "blocks_continuous_automation": False,
            "blocks_lessons_runtime_intelligence": False,
            "advisory_only": True,
            "detail": "Avenue adapters report capability gaps until NTE/Kalshi/Tastytrade are delegated without faking success.",
        }
    ]

    avenue_a: List[Dict[str, Any]] = []
    if isinstance(gate_b_gaps.get("items"), list):
        avenue_a.extend(
            {
                **item,
                "avenue_id": "A",
            }
            for item in gate_b_gaps["items"]
            if isinstance(item, dict)
        )

    avenue_b = [
        {
            "id": "kalshi_universal_truth_cycle",
            "classification": "avenue_specific",
            "avenue_id": "B",
            "blocks_live_orders_now": True,
            "blocks_repeated_ticks": True,
            "blocks_continuous_automation": True,
            "blocks_lessons_runtime_intelligence": False,
            "advisory_only": False,
            "detail": "Kalshi universal round-trip + normalized persistence not wired to execute_round_trip_with_truth.",
        }
    ]
    avenue_c = [
        {
            "id": "tastytrade_universal_truth_cycle",
            "classification": "avenue_specific",
            "avenue_id": "C",
            "blocks_live_orders_now": True,
            "blocks_repeated_ticks": True,
            "blocks_continuous_automation": True,
            "blocks_lessons_runtime_intelligence": False,
            "advisory_only": False,
            "detail": "Tastytrade fills + PnL not wired to universal orchestrator.",
        }
    ]

    return {
        "truth_version": "universal_remaining_gaps_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "universal_gaps": universal,
        "avenue_gaps": {"A": avenue_a, "B": avenue_b, "C": avenue_c},
        "gate_b_remaining_gaps_ref": str(ctrl / "gate_b_remaining_gaps_final.json"),
        "lessons_runtime_reads": bool(lessons.get("runtime_reads_lessons")),
        "honesty": "Gate B gaps are merged under avenue A as structured context; they remain authoritative for Coinbase Gate B.",
    }


def write_universal_remaining_gaps_artifact(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_universal_remaining_gaps(runtime_root=root)
    path = ctrl / "universal_remaining_gaps.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return {"path_json": str(path), "written": True, "payload": payload}
