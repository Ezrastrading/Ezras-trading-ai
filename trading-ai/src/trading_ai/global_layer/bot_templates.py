"""Bot templates — default permissions, memory files, scoring hooks, promotion requirements."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.global_layer.bot_permissions import default_permission_matrix
from trading_ai.global_layer.bot_types import BotRole
from trading_ai.global_layer.bot_validation_pipeline import validation_stages


def template_for_role(role: str) -> Dict[str, Any]:
    perms = default_permission_matrix().get(role, {})
    return {
        "role": role,
        "default_permissions": {k: list(v) if hasattr(v, "__iter__") and not isinstance(v, str) else v for k, v in perms.items()},
        "memory_files": ["performance.json", "trades.json", "lessons.json"],
        "scoring_keys": [
            "utility_score",
            "efficiency_score",
            "trust_score",
            "promotion_score",
        ],
        "required_tests": list(validation_stages()),
        "promotion_requirements": {
            "min_promotion_score": 0.65,
            "min_shadow_cycles": 3,
        },
    }


def all_templates() -> Dict[str, Any]:
    return {r.value: template_for_role(r.value) for r in BotRole}
