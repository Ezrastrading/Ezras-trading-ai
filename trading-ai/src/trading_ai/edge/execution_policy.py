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


def _validated_for_capital(status: str) -> bool:
    return status in (EdgeStatus.VALIDATED.value, EdgeStatus.SCALED.value)


def edge_allowed_in_regime(regime_tags: Any, current_regime: str) -> bool:
    """If ``regime_tags`` is empty, allow any regime; else ``current_regime`` must match (or tag ``any``)."""
    if regime_tags is None:
        return True
    if isinstance(regime_tags, dict):
        regime_tags = regime_tags.get("tags") or regime_tags.get("regime_tags")
    if not regime_tags:
        return True
    cr = (current_regime or "").strip().lower()
    if isinstance(regime_tags, (list, tuple, set)):
        tags = {str(x).strip().lower() for x in regime_tags if str(x).strip()}
    else:
        one = str(regime_tags).strip().lower()
        tags = {one} if one else set()
    if not tags:
        return True
    return cr in tags or "any" in tags


def _size_scale_for_edge_status(status: str) -> float:
    """Validated/scaled → full allocation weight (1.0); anything else → testing multiplier."""
    if _validated_for_capital(status):
        return 1.0
    return _testing_size_mult()


@dataclass
class EdgeAssignment:
    edge_id: Optional[str]
    edge_lane: str  # validated | testing | none
    size_scale: float
    allow_full_size: bool
    detail: str = ""
    edge_status: Optional[str] = None


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
    return EdgeAssignment(None, "none", 1.0, True, f"avenue_not_wired:{a}", edge_status=None)


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
        return EdgeAssignment(None, "none", 1.0, True, "no_edges_registered", edge_status=None)

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
        sm = _size_scale_for_edge_status(e.status)
        return EdgeAssignment(
            e.edge_id,
            "testing",
            sm,
            False,
            "testing_bucket",
            edge_status=e.status,
        )

    if validated:
        # Prefer strategy-linked match
        linked = [e for e in validated if e.linked_strategy_id == strategy_name]
        pick = linked[0] if linked else validated[0]
        sm = _size_scale_for_edge_status(pick.status)
        return EdgeAssignment(pick.edge_id, "validated", sm, True, "validated_pool", edge_status=pick.status)

    # Only candidate/testing-without-slot — still tag testing if promoted manually
    if testing:
        e = testing[0]
        sm = _size_scale_for_edge_status(e.status)
        return EdgeAssignment(e.edge_id, "testing", sm, False, "testing_only_pool", edge_status=e.status)

    if candidates and not _enforce_validated_for_scale():
        e = candidates[0]
        sm = _size_scale_for_edge_status(e.status)
        return EdgeAssignment(e.edge_id, "testing", sm, False, "candidate_observation", edge_status=e.status)

    if _enforce_validated_for_scale():
        return EdgeAssignment(None, "none", 0.0, False, "enforce_no_unvalidated_scale", edge_status=None)

    return EdgeAssignment(None, "none", 1.0, True, "legacy_untagged", edge_status=None)
