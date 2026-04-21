"""Execution priority ordering for Upside layer (lower rank = higher priority)."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# 1 = highest priority
PRIORITY_LATENCY_AND_VALIDATED = 1
PRIORITY_VALIDATED = 2
PRIORITY_TESTING = 3
PRIORITY_CANDIDATE = 4
PRIORITY_NONE = 5


def execution_rank_from_parts(
    *,
    has_latency_signal: bool,
    edge_lane: str,
    edge_status: Optional[str],
) -> int:
    lane = (edge_lane or "").strip().lower()
    st = (edge_status or "").strip().lower()
    validated = lane == "validated" or st in ("validated", "scaled")
    testing = lane == "testing" or st == "testing"
    candidate = st == "candidate" or lane == "candidate"

    if validated and has_latency_signal:
        return PRIORITY_LATENCY_AND_VALIDATED
    if validated:
        return PRIORITY_VALIDATED
    if testing:
        return PRIORITY_TESTING
    if candidate:
        return PRIORITY_CANDIDATE
    return PRIORITY_NONE


def sort_key_for_nt_product(
    *,
    execution_rank: int,
    latency_strength: float,
) -> Tuple[int, float]:
    """Sort ascending: rank first (1 before 4), then stronger latency first."""
    return (execution_rank, -float(latency_strength))


def sort_product_ids_by_priority(
    items: Sequence[Tuple[str, Tuple[int, float]]],
) -> List[str]:
    """
    ``items`` are (product_id, (execution_rank, -latency_strength)) from :func:`sort_key_for_nt_product`.
    """
    ordered = sorted(items, key=lambda x: x[1])
    return [x[0] for x in ordered]
