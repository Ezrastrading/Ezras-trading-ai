"""Promotion gates for bots — replay → shadow → advisory → supervised live (checklist only here)."""

from __future__ import annotations

from typing import Any, Dict, List


def validation_stages() -> List[str]:
    return [
        "historical_replay",
        "shadow_mode",
        "advisory_mode",
        "supervised_live",
    ]


def evaluate_promotion_readiness(bot: Dict[str, Any], stage_results: Dict[str, bool]) -> Dict[str, Any]:
    """
    stage_results maps stage -> passed. No automatic live promotion from this module.
    """
    stages = validation_stages()
    passed = [s for s in stages if stage_results.get(s)]
    ready_next = len(passed) == len(stages)
    return {
        "truth_version": "bot_validation_pipeline_v1",
        "bot_id": bot.get("bot_id"),
        "stages": stages,
        "passed": passed,
        "ready_for_next_phase": ready_next,
        "honesty": "Does not grant venue authority; execution remains in existing Gate A/B pipeline.",
    }
