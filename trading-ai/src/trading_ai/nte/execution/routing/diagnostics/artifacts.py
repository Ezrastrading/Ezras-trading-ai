"""Write inspectable JSON artifacts under EZRAS_RUNTIME_ROOT/data/routing/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from trading_ai.nte.execution.routing.core.product_graph import SpotAssetGraph
from trading_ai.nte.execution.routing.core.path_search import find_asset_paths
from trading_ai.nte.execution.routing.venues.coinbase.catalog import (
    build_coinbase_spot_graph_edges,
)
from trading_ai.runtime_paths import ezras_runtime_root


def write_product_graph_snapshot(runtime_root: Path | None = None) -> str:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    out_dir = root / "data" / "routing"
    out_dir.mkdir(parents=True, exist_ok=True)
    edges = build_coinbase_spot_graph_edges()
    g = SpotAssetGraph(edges)
    payload = {
        "graph_stats": g.stats(),
        "edge_count": len(edges),
        "sample_edges": [
            {
                "product_id": e.product_id,
                "base": e.base_asset,
                "quote": e.quote_asset,
                "liquidity_proxy": e.liquidity_proxy,
            }
            for e in edges[:80]
        ],
    }
    p = out_dir / "product_graph_snapshot.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(p)


def write_route_search_diagnostics(
    source: str,
    target: str,
    *,
    max_legs: int = 3,
    runtime_root: Path | None = None,
) -> str:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    out_dir = root / "data" / "routing"
    out_dir.mkdir(parents=True, exist_ok=True)
    edges = build_coinbase_spot_graph_edges()
    g = SpotAssetGraph(edges)
    paths = find_asset_paths(g, source, target, max_legs=max_legs)
    payload = {
        "source": source.upper(),
        "target": target.upper(),
        "max_legs": max_legs,
        "paths_found": len(paths),
        "paths_product_ids": paths[:50],
    }
    p = out_dir / "route_search_diagnostics.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(p)


def write_portfolio_truth_snapshot(client: Any, runtime_root: Path | None = None) -> str:
    from trading_ai.nte.execution.routing.core.portfolio_truth import build_portfolio_truth_coinbase

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    out_dir = root / "data" / "routing"
    out_dir.mkdir(parents=True, exist_ok=True)
    pt = build_portfolio_truth_coinbase(client)
    payload = {
        "total_marked_usd": pt.total_marked_usd,
        "liquid_quote_usd": pt.liquid_quote_usd,
        "rows": [
            {
                "currency": r.currency,
                "available": r.available,
                "mark_usd": r.mark_usd,
                "dust": r.dust,
            }
            for r in pt.rows
        ],
        "notes": pt.notes,
    }
    p = out_dir / "portfolio_truth_snapshot.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(p)
