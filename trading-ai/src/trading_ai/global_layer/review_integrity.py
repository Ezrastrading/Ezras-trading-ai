"""Review integrity state: full | degraded | failed."""

from __future__ import annotations

from enum import Enum


class ReviewIntegrityState(str, Enum):
    FULL = "full"
    DEGRADED = "degraded"
    FAILED = "failed"


def classify_integrity(
    packet_valid: bool,
    claude_usable: bool,
    gpt_usable: bool,
) -> ReviewIntegrityState:
    if not packet_valid:
        return ReviewIntegrityState.FAILED
    if claude_usable and gpt_usable:
        return ReviewIntegrityState.FULL
    if claude_usable or gpt_usable:
        return ReviewIntegrityState.DEGRADED
    return ReviewIntegrityState.FAILED
