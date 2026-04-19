"""Rank acceleration options — prefer allocation + execution, not raw risk."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_acceleration_options(
    *,
    best_avenue: Optional[str],
    worst_avenue: Optional[str],
) -> List[Dict[str, Any]]:
    opts: List[Dict[str, Any]] = []
    if best_avenue:
        opts.append(
            {
                "name": f"allocate_more_to_{best_avenue}",
                "type": "allocation",
                "expected_speed_gain_pct": 22.0,
                "risk_cost": "low",
                "recommended": True,
                "reason": "Shift capital toward the strongest current contributor.",
            }
        )
    if worst_avenue:
        opts.append(
            {
                "name": f"reduce_exposure_to_{worst_avenue}",
                "type": "allocation",
                "expected_speed_gain_pct": 15.0,
                "risk_cost": "low",
                "recommended": True,
                "reason": "Trim drag from weakest avenue while evidence is weak.",
            }
        )
    opts.append(
        {
            "name": "improve_net_after_fees",
            "type": "execution",
            "expected_speed_gain_pct": 20.0,
            "risk_cost": "low",
            "recommended": True,
            "reason": "Tighten maker entries and fee awareness — often 20%+ effective speed.",
        }
    )
    opts.append(
        {
            "name": "sandbox_new_strategies_only",
            "type": "research",
            "expected_speed_gain_pct": 10.0,
            "risk_cost": "medium",
            "recommended": True,
            "reason": "Test edges before capital; avoids fantasy acceleration.",
        }
    )
    return opts
