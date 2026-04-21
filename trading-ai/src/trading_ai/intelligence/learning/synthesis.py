"""Cross-domain synthesis for daily reviews — advisory text only."""

from __future__ import annotations

from typing import Any, Dict, List


def synthesize_learning_priorities(
    domain_docs: List[Dict[str, Any]],
    *,
    max_items: int = 12,
) -> Dict[str, Any]:
    """Rank thin-confidence domains for research tickets — no execution directives."""
    thin = sorted(
        domain_docs,
        key=lambda d: float(d.get("confidence") or 0),
    )[:max_items]
    return {
        "priority_domains": [d.get("domain") for d in thin],
        "notes": "Priorities are conservative — confirm with tickets and operator.",
    }
