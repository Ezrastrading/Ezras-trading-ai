"""Gate global promotion of lessons — require explicit generalization approval."""

from __future__ import annotations

from typing import Any, Dict, List


def approve_global_promotion(
    *,
    lesson: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    min_avenues: int = 2,
) -> bool:
    """
    Return True only if the lesson is supported across enough avenues or has
    strong cross-venue evidence (stub rules for tests; extend with CEO gates).
    """
    if not lesson:
        return False
    avs = {str(e.get("avenue")) for e in evidence if e.get("avenue")}
    if len(avs) >= min_avenues:
        return True
    if lesson.get("force_approved") is True:
        return True
    return False
