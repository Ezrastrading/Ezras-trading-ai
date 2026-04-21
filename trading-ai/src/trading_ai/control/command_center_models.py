"""
Typed shapes for command center snapshots (documentation + light validation helpers).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


class AlertItem(TypedDict, total=False):
    level: Literal["CRITICAL", "WARNING", "INFO"]
    message: str
    source: str


def empty_snapshot(ts: str) -> Dict[str, Any]:
    return {
        "timestamp": ts,
        "system_health": {},
        "deployment_status": {},
        "risk_state": {},
        "governance_state": {},
        "portfolio_state": {},
        "venue_state": {},
        "edge_state": {},
        "execution_state": {},
        "performance_state": {},
        "learning_state": {},
        "ceo_state": {},
        "alerts": [],
    }


def merge_section(base: Dict[str, Any], key: str, section: Dict[str, Any]) -> None:
    if section:
        base[key] = section
