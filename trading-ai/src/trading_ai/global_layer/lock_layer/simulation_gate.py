"""Simulation / replay gate before a spawned bot becomes active (no venue I/O)."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from trading_ai.global_layer.orchestration_schema import promotion_tier_index


def activation_simulation_required(bot: Dict[str, Any]) -> Tuple[bool, str]:
    """
    New bots must pass shadow + recorded replay eligibility before leaving lowest rungs.
    Uses registry flags set by offline harness (when wired).
    """
    flags = bot.get("simulation_flags") if isinstance(bot.get("simulation_flags"), dict) else {}
    replay_ok = bool(flags.get("historical_replay_ok"))
    shadow_ok = bool(flags.get("shadow_evaluation_ok"))
    paper_ok = bool(flags.get("paper_validation_ok"))
    pt = promotion_tier_index(str(bot.get("promotion_tier") or "T0"))
    if pt <= 1:
        return True, "low_tier_no_activation_gate"
    if not (replay_ok and shadow_ok and paper_ok):
        return False, "simulation_pipeline_incomplete"
    return True, "ok"
