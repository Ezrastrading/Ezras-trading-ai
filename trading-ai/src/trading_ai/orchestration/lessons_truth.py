"""Honest fields for whether self-learning / lessons affect runtime decisions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from trading_ai.intelligence.preflight import trading_intelligence_enabled
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def build_lessons_influence_truth(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    """
    Default: lessons are stored/reviewed; runtime gating is via intelligence preflight when enabled.

    Gate B also applies ``lesson_runtime_influence`` — see ``data/control/lessons_runtime_effect.json``.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    mem = ad.read_json("data/learning/trading_memory.json") or {}
    eff = ad.read_json("data/control/lessons_runtime_effect.json") or {}

    intel_on = trading_intelligence_enabled()
    gate_b_rank = bool(eff.get("influenced_ranking"))
    gate_b_entry = bool(eff.get("influenced_entry"))
    gate_b_exit = bool(eff.get("influenced_exit"))
    gate_b_rebuy = bool(eff.get("influenced_rebuy"))
    proven = bool(eff.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"))
    return {
        "lessons_runtime_intelligence_ready": bool(intel_on),
        "lessons_affect_candidate_ranking": gate_b_rank or bool(intel_on),
        "lessons_affect_entry_decisions": gate_b_entry or bool(intel_on),
        "lessons_affect_exit_decisions": gate_b_exit or bool(intel_on),
        "lessons_affect_rebuy_decisions": gate_b_rebuy or bool(intel_on),
        "LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN": proven or bool(intel_on),
        "lessons_runtime_effect_artifact": eff,
        "honesty_note": (
            "When EZRAS_TRADING_INTELLIGENCE is off, lessons/reports do not gate runtime orders; "
            "they may still be persisted under data/learning for review."
        ),
        "learning_storage_present": bool(mem),
        "policy": "no_silent_lesson_override",
    }
