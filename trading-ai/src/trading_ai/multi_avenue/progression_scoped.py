"""Scoped progression / goals / milestones — templates only; does not alter Avenue A behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _pace_block() -> Dict[str, Any]:
    return {
        "target_units": None,
        "current_units": None,
        "pace_required": None,
        "contribution_breakdown": {
            "by_avenue": {},
            "by_gate": {},
            "by_strategy": {},
        },
    }


def build_progression_payload(
    *,
    scope_level: str,
    avenue_id: Optional[str] = None,
    gate_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "artifact": "progression_scoped",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope_level": scope_level,
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "main_mission": _pace_block(),
        "milestones": [],
        "accumulation": {"note": "Attach venue-specific balances only inside scoped avenue blocks."},
        "honesty": "Template only — numeric goals remain operator-defined.",
    }
