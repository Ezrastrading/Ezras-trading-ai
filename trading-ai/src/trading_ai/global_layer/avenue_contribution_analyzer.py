"""Rank avenues by net contribution from aggregated PnL."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def best_worst_avenue(by_avenue: Dict[str, float]) -> Tuple[Optional[str], Optional[str]]:
    if not by_avenue:
        return None, None
    items = list(by_avenue.items())
    items.sort(key=lambda x: x[1])
    return items[-1][0], items[0][0]


def contribution_summary(by_avenue: Dict[str, float]) -> Dict[str, Any]:
    best, worst = best_worst_avenue(by_avenue)
    total = sum(by_avenue.values())
    return {
        "best_avenue": best,
        "worst_avenue": worst,
        "total_net_usd": total,
        "per_avenue": dict(by_avenue),
    }
