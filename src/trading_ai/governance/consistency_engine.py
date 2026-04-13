"""Cross-signal consistency checks — advisory; doctrine remains authoritative."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ConsistencyRecord:
    module: str
    signal_id: str
    consistent_with_doctrine: bool = True
    notes: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


_history: List[ConsistencyRecord] = []


def record_signal_consistency(rec: ConsistencyRecord) -> None:
    _history.append(rec)


def recent_inconsistencies(limit: int = 50) -> List[ConsistencyRecord]:
    return list(_history[-limit:])


def clear_test_history() -> None:
    _history.clear()
