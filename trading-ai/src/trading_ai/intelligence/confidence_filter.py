"""Confidence gate — skip low-conviction entries."""

from typing import Optional, Tuple


def passes_confidence(confidence_score: float) -> Tuple[bool, Optional[str]]:
    if confidence_score < 0.7:
        return False, "low_confidence"
    return True, None
