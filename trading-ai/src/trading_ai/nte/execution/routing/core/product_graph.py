"""Undirected asset graph from real product edges (one edge per product)."""

from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Dict, List, Set, Tuple

from trading_ai.nte.execution.routing.core.universal_types import AssetId, UniversalProductEdge


class SpotAssetGraph:
    """
    Assets are nodes; each product is an undirected edge between base and quote.

    Multi-leg routes are simple paths in this graph (each step uses one real ``product_id``).
    """

    def __init__(self, edges: List[UniversalProductEdge]) -> None:
        self.edges_by_product: Dict[str, UniversalProductEdge] = {
            e.product_id.upper(): e for e in edges
        }
        self._adj: DefaultDict[AssetId, List[Tuple[AssetId, str]]] = defaultdict(list)
        for e in edges:
            b = e.base_asset.upper()
            q = e.quote_asset.upper()
            self._adj[b].append((q, e.product_id))
            self._adj[q].append((b, e.product_id))

    def neighbors(self, asset: AssetId) -> List[Tuple[AssetId, str]]:
        return list(self._adj.get(asset.upper(), []))

    def stats(self) -> Dict[str, int]:
        assets: Set[str] = set(self._adj.keys())
        for a, nbrs in self._adj.items():
            for nb, _ in nbrs:
                assets.add(nb)
        return {
            "asset_count": len(assets),
            "edge_count": len(self.edges_by_product),
            "directed_adj_entries": sum(len(v) for v in self._adj.values()),
        }
