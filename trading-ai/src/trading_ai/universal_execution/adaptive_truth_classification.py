"""
Classify adaptive / governance blockers — stale persisted vs authoritative (callers attach evidence).
"""

from __future__ import annotations

from enum import Enum
from typing import Dict


class LiveEntryBlockerClass(str, Enum):
    HARD_TECHNICAL = "hard_technical"
    ADAPTIVE_GATE = "adaptive_gate"
    ADAPTIVE_AVENUE = "adaptive_avenue"
    ADAPTIVE_GLOBAL = "adaptive_global"
    GOVERNANCE = "governance"
    STALE_PERSISTED_NON_AUTHORITATIVE = "stale_persisted_non_authoritative"


def describe_stale_persisted_note(note: str) -> Dict[str, str]:
    return {"classification": LiveEntryBlockerClass.STALE_PERSISTED_NON_AUTHORITATIVE.value, "note": note}
