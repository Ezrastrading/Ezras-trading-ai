"""Execution-facing edge resolution — no strategy_research imports."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from trading_ai.edge.models import EdgeStatus
from trading_ai.edge.registry import EdgeRegistry

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def _testing_routing_fraction() -> float:
    return max(0.0, min(1.0, _env_float("EDGE_TESTING_ROUTING_FRACTION", 0.08)))


def _testing_size_mult() -> float:
    return max(0.0, _env_float("EDGE_TESTING_SIZE_MULTIPLIER", 0.12))


def _enforce_validated_for_scale() -> bool:
    return (os.environ.get("EDGE_ENFORCE_VALIDATED_FOR_SCALE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


@dataclass
class EdgeAssignment:
    edge_id: Optional[str]
    edge_lane: str  # validated | testing | none
    size_scale: float
    allow_full_size: bool
    detail: str = ""


def _hash_slot(strategy: str, product_id: str) -> float:
    h = hashlib.sha256(f"{strategy}|{product_id}|edge_route".encode()).hexdigest()
    return (int(h[:12], 16) % 10_000) / 10_000.0


def resolve_edge_for_avenue(
    avenue: str,
    strategy_name: str,
    product_id: str,
    *,
    registry: Optional[EdgeRegistry] = None,
) -> EdgeAssignment:
    """
    Route by venue. Coinbase is implemented; other venues return untagged until wired.
    """
    a = (avenue or "").strip().lower()
    if a in ("coinbase", "a"):
        return resolve_coinbase_edge(strategy_name, product_id, registry=registry)
    return EdgeAssignment(None, "none", 1.0, True, f"avenue_not_wired:{a}")


def resolve_coinbase_edge(
    strategy_name: str,
    product_id: str,
    *,
    registry: Optional[EdgeRegistry] = None,
) -> EdgeAssignment:
    """
    Pick an edge for tagging + size scaling. Does not assume profitability.

    - If registry empty: untagged (lane ``none``), full size allowed (legacy).
    - Testing lane: small ``size_scale``; never treated as validated for scale.
    - Validated/scaled: full size subject to global multipliers elsewhere.
    """
    reg = registry or EdgeRegistry()
    all_e = reg.list_edges()
    avenue = "coinbase"
    by_avenue = [e for e in all_e if e.avenue == avenue and e.status != EdgeStatus.REJECTED.value]
    if not by_avenue:
        return EdgeAssignment(None, "none", 1.0, True, "no_edges_registered")

    validated = [
        e
        for e in by_avenue
        if e.status in (EdgeStatus.VALIDATED.value, EdgeStatus.SCALED.value)
        and (e.linked_strategy_id is None or e.linked_strategy_id == strategy_name)
    ]
    testing = [
        e
        for e in by_avenue
        if e.status == EdgeStatus.TESTING.value
        and (e.linked_strategy_id is None or e.linked_strategy_id == strategy_name)
    ]
    candidates = [e for e in by_avenue if e.status == EdgeStatus.CANDIDATE.value]

    slot = _hash_slot(strategy_name, product_id)
    test_frac = _testing_routing_fraction()
    if testing and slot < test_frac:
        e = testing[0]
        return EdgeAssignment(
            e.edge_id,
            "testing",
            _testing_size_mult(),
            False,
            "testing_bucket",
        )

    if validated:
        # Prefer strategy-linked match
        linked = [e for e in validated if e.linked_strategy_id == strategy_name]
        pick = linked[0] if linked else validated[0]
        return EdgeAssignment(pick.edge_id, "validated", 1.0, True, "validated_pool")

    # Only candidate/testing-without-slot — still tag testing if promoted manually
    if testing:
        e = testing[0]
        return EdgeAssignment(e.edge_id, "testing", _testing_size_mult(), False, "testing_only_pool")

    if candidates and not _enforce_validated_for_scale():
        e = candidates[0]
        return EdgeAssignment(e.edge_id, "testing", _testing_size_mult(), False, "candidate_observation")

    if _enforce_validated_for_scale():
        return EdgeAssignment(None, "none", 0.0, False, "enforce_no_unvalidated_scale")

    return EdgeAssignment(None, "none", 1.0, True, "legacy_untagged")
