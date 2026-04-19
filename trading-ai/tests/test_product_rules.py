"""Product increment / notional validation for live Coinbase orders."""

from __future__ import annotations

from trading_ai.nte.execution.product_rules import validate_order_size


def test_btc_notional_below_min_rejected():
    ok, reason = validate_order_size("BTC-USD", quote_notional_usd=5.0)
    assert ok is False
    assert reason and "notional" in reason


def test_btc_valid_base_ok():
    ok, _ = validate_order_size("BTC-USD", base_size="0.00002")
    assert ok is True


def test_btc_bad_increment_rejected():
    ok, reason = validate_order_size("BTC-USD", base_size="0.0000200003")
    assert ok is False
    assert reason == "base_increment_mismatch"


def test_unknown_product_passes():
    ok, _ = validate_order_size("SOL-USD", base_size="0.001")
    assert ok is True
