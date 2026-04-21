"""Multi-leg path search — BFS, cycle avoidance, bounded branching."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from trading_ai.nte.execution.routing.core.product_graph import SpotAssetGraph
from trading_ai.nte.execution.routing.core.universal_types import AssetId


def find_asset_paths(
    graph: SpotAssetGraph,
    source: AssetId,
    target: AssetId,
    *,
    max_legs: int = 3,
    max_paths: int = 24,
) -> List[List[str]]:
    """
    Return up to ``max_paths`` simple paths (no repeated asset) from ``source`` to ``target``.

    Each path is a list of **product_id** strings in order (one leg per hop).
    """
    src = source.upper()
    tgt = target.upper()
    if src == tgt:
        return [[]]

    if max_legs < 1:
        return []

    # BFS on (current_asset, path_products, visited_assets)
    out: List[List[str]] = []
    frontier: List[Tuple[AssetId, List[str], frozenset]] = [(src, [], frozenset({src}))]
    while frontier and len(out) < max_paths:
        next_frontier: List[Tuple[AssetId, List[str], frozenset]] = []
        for cur, prod_path, seen in frontier:
            if len(prod_path) >= max_legs:
                continue
            for nbr, pid in graph.neighbors(cur):
                if nbr in seen:
                    continue
                new_path = prod_path + [pid]
                new_seen = seen | {nbr}
                if nbr == tgt:
                    out.append(new_path)
                    if len(out) >= max_paths:
                        return out
                else:
                    next_frontier.append((nbr, new_path, new_seen))
        frontier = next_frontier

    return out
