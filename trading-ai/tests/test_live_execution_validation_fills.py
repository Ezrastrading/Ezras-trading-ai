"""Legacy shim: buy-fill parsing tests now live under ``test_coinbase_spot_fill_truth``."""

from __future__ import annotations

import pytest

from trading_ai.nte.execution.product_rules import round_base_to_increment
from trading_ai.runtime_proof.coinbase_spot_fill_truth import normalize_coinbase_buy_fills


def test_quote_sized_buy_fill_size_matches_usd_not_base() -> None:
    agg = normalize_coinbase_buy_fills(
        "BTC-USD",
        [{"price": "100000.0", "size": "9.87984212", "filled_value": "9.87984212"}],
    )
    assert agg.buy_base_qty == pytest.approx(9.87984212 / 100_000.0, rel=1e-9)
    assert agg.buy_base_qty < 0.001


def test_sum_buy_fills_prefers_quote_over_misleading_size() -> None:
    agg = normalize_coinbase_buy_fills(
        "BTC-USD",
        [{"price": "95000.0", "size": "10.0", "filled_value": "10.0", "commission": "0.02"}],
    )
    assert agg.buy_quote_spent == pytest.approx(10.0)
    assert agg.buy_base_qty == pytest.approx(10.0 / 95_000.0, rel=1e-9)
    assert agg.buy_base_qty < 0.001
    assert agg.fees_buy_usd == pytest.approx(0.02)


def test_sum_buy_fills_when_size_is_already_base() -> None:
    agg = normalize_coinbase_buy_fills(
        "BTC-USD",
        [{"price": "100000.0", "size": "0.0001", "filled_value": "10.0"}],
    )
    assert agg.buy_base_qty == pytest.approx(0.0001, rel=1e-9)


def test_flatten_sell_string_is_tiny_btc_for_ten_dollar_buy() -> None:
    agg = normalize_coinbase_buy_fills(
        "BTC-USD",
        [{"price": "100000", "size": "10", "filled_value": "10"}],
    )
    s = round_base_to_increment("BTC-USD", agg.buy_base_qty)
    val = float(s)
    assert val < 0.01
    assert val < 1.0
    assert not (val > 1.0), "sell base_size must not look like USD notional"
