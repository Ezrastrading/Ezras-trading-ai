"""NTE Coinbase products defaults and env override."""

from __future__ import annotations

from trading_ai.nte.config.settings import load_nte_settings
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import (
    resolve_coinbase_runtime_product_policy,
)


def test_default_products_include_btc_usdc_for_usdc_funded_paths() -> None:
    s = load_nte_settings()
    assert "BTC-USD" in s.products
    assert "BTC-USDC" in s.products
    assert "ETH-USD" in s.products
    assert "ETH-USDC" in s.products
    assert "SOL-USD" in s.products
    assert "SOL-USDC" in s.products
    assert "AVAX-USD" in s.products
    assert "LINK-USD" in s.products
    assert len(s.products) >= 8


def test_ntenv_products_override(monkeypatch) -> None:
    monkeypatch.setenv("NTE_PRODUCTS", "BTC-USD,SOL-USD")
    s = load_nte_settings()
    assert s.products == ("BTC-USD", "SOL-USD")


def test_env_override_excludes_btc_usdc_from_runtime_truth(monkeypatch) -> None:
    monkeypatch.setenv("NTE_PRODUCTS", "BTC-USD,ETH-USD")
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    assert "BTC-USDC" not in pol.runtime_active_products
    assert pol.effective_products_source == "env_override"
    assert pol.products_removed_by_env and "BTC-USDC" in pol.products_removed_by_env
    ex = pol.explain_product("BTC-USDC")
    assert ex["in_runtime_active_products"] is False
    assert ex["env_override_active"] is True
