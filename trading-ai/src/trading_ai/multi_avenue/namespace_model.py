"""
Canonical namespace and scope model — every artifact/session must declare its scope explicitly.

Layer 1: universal intelligence (reusable patterns).
Layer 2: avenue/gate/venue execution (this package does not implement execution).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict


class ScopeLevel(str, Enum):
    """Structural scope for artifacts, sessions, and reviews."""

    SYSTEM = "system"
    AVENUE = "avenue"
    GATE = "gate"
    STRATEGY = "strategy"
    SCANNER = "scanner"
    TRADE = "trade"


class SessionScope(str, Enum):
    """CEO / review session aggregation."""

    SYSTEM_WIDE = "system_wide"
    AVENUE = "avenue"
    GATE = "gate"
    CROSS_AVENUE = "cross_avenue"
    STRATEGY = "strategy"


class ArtifactScope(str, Enum):
    """Where an artifact file logically belongs."""

    SYSTEM = "system"
    AVENUE = "avenue"
    GATE = "gate"
    STRATEGY = "strategy"


class NamespaceKeys(TypedDict, total=False):
    """Required identifiers for scoped payloads (extend in callers; all optional for partial docs)."""

    avenue_id: str
    avenue_name: str
    gate_id: str
    gate_name: str
    market_type: str
    venue_name: str
    strategy_id: str
    scanner_id: str
    edge_id: str
    edge_lane: str
    session_scope: str
    review_scope: str
    artifact_scope: str
    scope_level: str


CANONICAL_IDENTIFIER_DOCS: Dict[str, str] = {
    "avenue_id": "Short stable id (e.g. A, B) — never reuse across different venues.",
    "avenue_name": "Semantic name: coinbase_nte, kalshi, …",
    "gate_id": "Lowercase gate slug: gate_a, gate_b, …",
    "gate_name": "Human label for reports.",
    "market_type": "prediction_crypto, spot_crypto, …",
    "venue_name": "coinbase, kalshi, …",
    "strategy_id": "Strategy key as used in trade rows / registry.",
    "scanner_id": "Logical scanner id for registry / CEO routing.",
    "edge_id": "Edge registry id.",
    "edge_lane": "Lane / book / risk bucket for the edge.",
    "session_scope": "system_wide | avenue | gate | cross_avenue | strategy",
    "review_scope": "Matches artifact aggregation (daily/weekly/ratio/edge/…)",
    "artifact_scope": "system | avenue | gate | strategy — filesystem placement",
}


def minimal_scope_labels(
    *,
    scope_level: str,
    avenue_id: Optional[str] = None,
    gate_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Explicit scope block for JSON artifacts (no implicit scope)."""
    return {
        "scope_level": scope_level,
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "implicit_scope_forbidden": True,
    }


def structural_model_summary() -> Dict[str, Any]:
    """Documentation-only structural model for operators and tools."""
    return {
        "layer_1_universal_intelligence": [
            "goals",
            "progression_templates",
            "reviews",
            "CEO session shells",
            "ratio/reserve/deployable *patterns*",
            "databank schema patterns",
            "edge lifecycle *structures*",
            "research/scanner *framework*",
            "truth/audit matrices",
        ],
        "layer_2_avenue_gate_specific": [
            "execution",
            "fills",
            "latency",
            "venue product catalogs",
            "gate-specific scanner logic",
            "order guards",
            "route semantics",
        ],
        "scope_hierarchy": [
            "system → avenue → gate → strategy (and scanner/trade as cross-cutting)",
        ],
    }
