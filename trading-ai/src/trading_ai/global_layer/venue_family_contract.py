"""
Canonical venue-family → avenue membership for orchestration freeze (operator-visible, stable IDs).
"""

from __future__ import annotations

from typing import Any, Dict, List

# Family id → venue/avenue ids used in orchestration / execution (lowercase).
VENUE_FAMILY_MEMBERS: Dict[str, List[str]] = {
    "spot_crypto": ["coinbase"],
    "prediction_markets": ["kalshi"],
}

# Reverse lookup: avenue id → family (first match wins).
AVENUE_TO_VENUE_FAMILY: Dict[str, str] = {}
for _fam, _avs in VENUE_FAMILY_MEMBERS.items():
    for _a in _avs:
        AVENUE_TO_VENUE_FAMILY.setdefault(str(_a).strip().lower(), _fam)


def avenues_for_venue_family(family_id: str) -> List[str]:
    return list(VENUE_FAMILY_MEMBERS.get(str(family_id).strip(), []) or [])


def venue_family_for_avenue(avenue_id: str) -> str:
    return AVENUE_TO_VENUE_FAMILY.get(str(avenue_id).strip().lower(), "unknown")


def describe_venue_family_contract() -> Dict[str, Any]:
    return {
        "truth_version": "venue_family_contract_v1",
        "venue_family_members": dict(VENUE_FAMILY_MEMBERS),
        "honesty": "Families group execution venues for scoped orchestration freeze — extend when adding venues.",
    }
