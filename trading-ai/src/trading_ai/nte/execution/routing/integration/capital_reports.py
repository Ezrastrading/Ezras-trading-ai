"""
Deployable capital + route-selection artifacts — honest about single-leg execution vs search-only multi-leg.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from trading_ai.nte.execution.routing.core.path_search import find_asset_paths
from trading_ai.nte.execution.routing.core.product_graph import SpotAssetGraph
from trading_ai.nte.execution.routing.core.universal_types import UniversalProductEdge
from trading_ai.nte.execution.routing.integration.spot_quote_utils import (
    is_spot_like_product_id,
    parse_spot_base_quote,
)
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import CoinbaseRuntimeProductPolicy
from trading_ai.nte.execution.routing.policy.universal_runtime_policy import UniversalCryptoRuntimePolicy


def spot_graph_from_product_ids(
    product_ids: Sequence[str],
    *,
    venue: str = "coinbase",
) -> SpotAssetGraph:
    """Real product ids only — one undirected edge per spot pair."""
    edges: List[UniversalProductEdge] = []
    for pid in product_ids:
        if not is_spot_like_product_id(str(pid)):
            continue
        b, q = parse_spot_base_quote(str(pid))
        edges.append(
            UniversalProductEdge(
                venue=venue,
                product_id=str(pid).strip().upper(),
                base_asset=b,
                quote_asset=q,
                liquidity_proxy=1e9,
                healthy=True,
            )
        )
    return SpotAssetGraph(edges)


def _dust_usd(mark: Optional[float], threshold: float) -> bool:
    if mark is None:
        return False
    return 0 < float(mark) < threshold


def build_deployable_capital_report(
    *,
    bal_by_quote: Dict[str, float],
    all_balances: Optional[Dict[str, float]],
    pol: CoinbaseRuntimeProductPolicy,
    universal: UniversalCryptoRuntimePolicy,
    attempts: List[Dict[str, Any]],
    chosen_product_id: Optional[str],
    resolution_status: str,
    error_code: Optional[str],
    portfolio_total_mark_value_usd: Optional[float] = None,
    dust_threshold_usd: float = 1.0,
) -> Dict[str, Any]:
    """Machine + operator fields for Gate A/B, readiness, control room."""
    direct_quote = {k.upper(): float(v) for k, v in (bal_by_quote or {}).items()}
    all_b = {k.upper(): float(v) for k, v in (all_balances or {}).items()} if all_balances else {}

    direct_fundable = [str(x.get("product_id")) for x in attempts if x.get("executable_now")]
    fundable_disallowed = [
        str(x.get("product_id"))
        for x in attempts
        if x.get("quote_sufficient") and x.get("runtime_allowed") is False
    ]
    allowed_set = {p.upper() for p in pol.validation_active_products}
    allowed_unfundable = [
        str(x.get("product_id"))
        for x in attempts
        if str(x.get("product_id") or "").upper() in allowed_set
        and str(x.get("rejection_reason") or "") == "insufficient_quote_balance"
    ]

    policy_blocked_fundable = [
        {
            "product_id": str(x.get("product_id")),
            "quote_asset": x.get("quote_asset"),
            "available_quote_balance": x.get("available_quote_balance"),
        }
        for x in attempts
        if x.get("quote_sufficient") and x.get("runtime_allowed") is False
    ]

    # Convertible = non-quote asset with balance (would require at least one trade to reach a quote wallet).
    quote_syms = {"USD", "USDC", "USDT", "EUR", "GBP"}
    convertible_assets = [
        {"asset": a, "available_quantity": q, "dust": _dust_usd(None, dust_threshold_usd)}
        for a, q in sorted(all_b.items())
        if a not in quote_syms and q > 0
    ]

    conservative = float(direct_quote.get("USD", 0.0)) + float(direct_quote.get("USDC", 0.0))
    validation_cap = conservative
    live_cap = conservative

    return {
        "artifact": "deployable_capital_report",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_total_mark_value_usd": portfolio_total_mark_value_usd,
        "direct_quote_balances_by_asset": direct_quote,
        "all_wallet_balances_by_asset": all_b or None,
        "direct_single_leg_opportunities": direct_fundable,
        "convertible_route_opportunities": [],  # filled by route_selection_report when graph search runs
        "blocked_assets": list(universal.blocked_assets),
        "blocked_routes": [],
        "policy_blocked_but_fundable": policy_blocked_fundable,
        "liquidity_blocked": [
            str(x.get("product_id"))
            for x in attempts
            if str(x.get("rejection_reason") or "") == "venue_ticker_unhealthy_or_missing"
        ],
        "dust_ignored_preview": convertible_assets,
        "recommended_deployable_capital": {
            "scope": "single_leg_quote_wallets",
            "usd_plus_usdc": round(conservative, 8),
        },
        "conservative_deployable_capital": round(conservative, 8),
        "validation_deployable_capital": round(validation_cap, 8),
        "live_execution_deployable_capital": round(live_cap, 8),
        "resolution_status": resolution_status,
        "chosen_product_id": chosen_product_id,
        "error_code": error_code,
        "direct_vs_convertible_summary": {
            "direct_quote_assets": sorted(direct_quote.keys()),
            "non_quote_assets_with_balance": [c["asset"] for c in convertible_assets],
            "note": "Convertible assets require real venue trades to become quote — not auto-spent as cash.",
        },
        "policy_vs_capital_summary": {
            "fundable_but_runtime_disallowed_products": fundable_disallowed,
            "allowed_but_unfundable_products": allowed_unfundable,
        },
        "canonical_universal_runtime_policy": universal.to_dict(),
    }


def build_route_selection_report(
    *,
    pol: CoinbaseRuntimeProductPolicy,
    universal: UniversalCryptoRuntimePolicy,
    attempts: List[Dict[str, Any]],
    chosen_product_id: Optional[str],
    all_balances: Optional[Dict[str, float]],
    error_code: Optional[str],
) -> Dict[str, Any]:
    """
    Single-leg selection truth + optional multi-leg **search** results (never implies execution).
    """
    graph = spot_graph_from_product_ids(pol.effective_products or pol.runtime_active_products)
    targets = [q for q in universal.allowed_quotes if q in {"USD", "USDC"}]
    convertible_hints: List[Dict[str, Any]] = []

    bal_all = {k.upper(): float(v) for k, v in (all_balances or {}).items()} if all_balances else {}
    quote_syms = {"USD", "USDC", "USDT", "EUR", "GBP"}
    max_legs = universal.max_route_legs_search

    for asset, qty in sorted(bal_all.items()):
        if asset in quote_syms or qty <= 0:
            continue
        for tgt in targets:
            if asset == tgt:
                continue
            paths = find_asset_paths(graph, asset, tgt, max_legs=max_legs, max_paths=8)
            for path in paths:
                convertible_hints.append(
                    {
                        "source_asset": asset,
                        "target_asset": tgt,
                        "legs": path,
                        "leg_count": len(path),
                        "execution_status": "search_only_not_execution_enabled",
                        "blocking_reason_if_used_for_live": "multi_leg_not_enabled_for_execution",
                    }
                )

    single_leg_winner = chosen_product_id
    return {
        "artifact": "route_selection_report",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "chosen_route": {
            "kind": "single_leg_spot",
            "product_id": single_leg_winner,
            "legs": [single_leg_winner] if single_leg_winner else [],
        },
        "single_leg_candidate_table": attempts,
        "multi_leg_route_search": {
            "honest_status": universal.multi_leg_routing_honest_status,
            "search_enabled": universal.route_search_enabled,
            "max_legs": max_legs,
            "sample_paths_non_quote_to_quote": convertible_hints[:24],
        },
        "error_code": error_code,
        "notes": [
            "Multi-leg paths are graph search on runtime-allowed products only; no hidden conversion.",
            "Execution stack remains single-leg spot for validation and live micro-validation.",
        ],
    }


def build_portfolio_truth_snapshot_dict(
    *,
    rows: List[Dict[str, Any]],
    total_marked_usd: float,
    source_venue: str = "coinbase",
) -> Dict[str, Any]:
    """Normalized portfolio snapshot for ``data/control`` / ``data/routing``."""
    return {
        "artifact": "portfolio_truth_snapshot",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_venue": source_venue,
        "total_portfolio_mark_value_usd": round(total_marked_usd, 8),
        "wallet_rows": rows,
        "interpretation": {
            "total_mark_includes_best_effort_crypto_marks": True,
            "executable_capital_is_not_total_mark": True,
            "single_leg_execution_uses_quote_balances": True,
        },
    }


def normalized_wallet_rows_from_balances(
    *,
    quote_balances: Dict[str, float],
    all_balances: Optional[Dict[str, float]],
    mark_by_asset: Optional[Dict[str, float]],
) -> List[Dict[str, Any]]:
    """Shape aligned with operator / control-room expectations."""
    marks = mark_by_asset or {}
    keys = sorted(set(quote_balances.keys()) | set((all_balances or {}).keys()))
    out: List[Dict[str, Any]] = []
    for asset in keys:
        u = asset.upper()
        avail = float((all_balances or quote_balances).get(u, 0.0))
        if avail <= 0 and u not in marks:
            continue
        m = marks.get(u)
        stable = u in ("USDC", "USDT", "DAI")
        base_flag = u in ("USD", "EUR", "GBP")
        quote_like = u in ("USD", "USDC", "USDT", "EUR", "GBP")
        out.append(
            {
                "asset_symbol": u,
                "available_quantity": avail,
                "hold_quantity": None,
                "total_quantity": avail,
                "usd_mark_value": m,
                "source_venue": "coinbase",
                "account_type": "spot_wallet",
                "tradable": None,
                "convertible": not quote_like,
                "stablecoin_flag": stable,
                "base_currency_flag": base_flag,
                "raw_source_metadata": {},
            }
        )
    return out
