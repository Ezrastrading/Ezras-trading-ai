"""Simple regime label for Gate B sizing."""

from __future__ import annotations

from typing import Any, Dict


def detect_regime(**kwargs: Any) -> Dict[str, Any]:
    if kwargs.get("force_chop"):
        return {"regime": "chop", "gate_b_size_multiplier": 0.7}
    return {"regime": "trend", "gate_b_size_multiplier": 1.0}
