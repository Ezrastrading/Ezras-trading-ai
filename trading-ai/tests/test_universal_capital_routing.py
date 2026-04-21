"""Universal capital layer: defaults, deployable report, honest multi-leg status."""

from __future__ import annotations

from trading_ai.nte.config.settings import _default_nte_coinbase_products, load_nte_settings
from trading_ai.nte.execution.routing.core.path_search import find_asset_paths
from trading_ai.nte.execution.routing.core.product_graph import SpotAssetGraph
from trading_ai.nte.execution.routing.integration.capital_reports import (
    build_deployable_capital_report,
    spot_graph_from_product_ids,
)
from trading_ai.nte.execution.routing.core.universal_types import UniversalProductEdge
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import resolve_coinbase_runtime_product_policy
from trading_ai.nte.execution.routing.policy.universal_runtime_policy import build_universal_runtime_policy


def test_default_runtime_products_include_major_spot_pairs() -> None:
    d = _default_nte_coinbase_products()
    for p in ("BTC-USD", "BTC-USDC", "ETH-USD", "ETH-USDC", "SOL-USD", "SOL-USDC", "AVAX-USD", "LINK-USD"):
        assert p in d


def test_load_nte_settings_defaults_match_expanded_allowlist(monkeypatch: object) -> None:
    monkeypatch.delenv("NTE_PRODUCTS", raising=False)
    monkeypatch.delenv("NTE_COINBASE_PRODUCTS", raising=False)
    s = load_nte_settings()
    assert len(s.products) >= 8
    assert "BTC-USDC" in s.products


def test_deployable_report_direct_vs_convertible() -> None:
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    u = build_universal_runtime_policy(pol)
    attempts = [
        {
            "product_id": "BTC-USDC",
            "quote_sufficient": True,
            "runtime_allowed": False,
            "rejection_reason": "runtime_policy_disallows_product",
            "quote_asset": "USDC",
            "available_quote_balance": 50.0,
        }
    ]
    dcr = build_deployable_capital_report(
        bal_by_quote={"USD": 1.0, "USDC": 50.0},
        all_balances={"USD": 1.0, "USDC": 50.0, "ETH": 0.1},
        pol=pol,
        universal=u,
        attempts=attempts,
        chosen_product_id=None,
        resolution_status="blocked",
        error_code="runtime_policy_disallows_fundable_product",
    )
    dvc = dcr.get("direct_vs_convertible_summary") or {}
    assert "USDC" in (dvc.get("direct_quote_assets") or [])
    assert dcr.get("policy_blocked_but_fundable")


def test_multi_leg_graph_search_finds_path_without_execution_claim() -> None:
    edges = [
        UniversalProductEdge("coinbase", "ETH-USDC", "ETH", "USDC"),
        UniversalProductEdge("coinbase", "BTC-USDC", "BTC", "USDC"),
    ]
    g = SpotAssetGraph(edges)
    paths = find_asset_paths(g, "ETH", "USDC", max_legs=2)
    assert paths and paths[0] == ["ETH-USDC"]
    u = build_universal_runtime_policy(resolve_coinbase_runtime_product_policy(include_venue_catalog=False))
    assert u.multi_leg_route_execution_enabled is False
    assert u.multi_leg_execution_blocked_reason == "not_enabled_in_production"
    assert u.multi_leg_routing_honest_status == "search_only_not_execution_enabled"


def test_spot_graph_from_defaults_is_connected_for_search_only() -> None:
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    g = spot_graph_from_product_ids(pol.runtime_active_products)
    st = g.stats()
    assert st["edge_count"] >= 6


def test_canonical_universal_policy_lists_allowed_quotes() -> None:
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    u = build_universal_runtime_policy(pol)
    assert "USD" in u.allowed_quotes
    assert "USDC" in u.allowed_quotes
