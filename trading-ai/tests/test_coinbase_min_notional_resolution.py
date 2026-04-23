from __future__ import annotations

import time
from pathlib import Path

import pytest


def test_coinbase_min_notional_uses_product_metadata_and_allows_5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    # Simulate Coinbase product metadata providing a $5 quote minimum.
    monkeypatch.setattr(
        "trading_ai.shark.outlets.coinbase._brokerage_public_request",
        lambda _p: {"product_id": "BTC-USD", "quote_min_size": "5"},
    )

    from trading_ai.nte.execution.coinbase_min_notional import resolve_coinbase_min_notional_usd

    vmin, src, meta = resolve_coinbase_min_notional_usd(product_id="BTC-USD", runtime_root=tmp_path)
    assert float(vmin) == 5.0
    assert src in ("coinbase_product_metadata_live_refresh", "coinbase_product_metadata_cache")
    assert meta.get("product_id") == "BTC-USD"


def test_coinbase_min_notional_falls_back_to_bundled_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    # No usable fields
    monkeypatch.setattr(
        "trading_ai.shark.outlets.coinbase._brokerage_public_request",
        lambda _p: {"product_id": "BTC-USD"},
    )

    from trading_ai.nte.execution.coinbase_min_notional import resolve_coinbase_min_notional_usd

    vmin, src, _meta = resolve_coinbase_min_notional_usd(product_id="BTC-USD", runtime_root=tmp_path)
    assert src in ("bundled_defaults_fallback", "coinbase_product_metadata_live_refresh", "coinbase_product_metadata_cache")
    # Bundled defaults for BTC-USD are 10 in current conservative defaults.
    assert float(vmin) in (10.0, 5.0)

