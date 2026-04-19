"""Coinbase spot fill normalization, flatten guards, dry-run close path."""

from __future__ import annotations

import pytest

from trading_ai.runtime_proof.coinbase_spot_fill_truth import (
    FlattenSizeValidationError,
    dry_run_validation_close_from_fixtures,
    normalize_coinbase_buy_fills,
    validate_flatten_base_before_sell,
)
from trading_ai.nte.execution.product_rules import round_base_to_increment


def test_quote_notional_mistaken_for_base_normalizes_to_tiny_btc() -> None:
    agg = normalize_coinbase_buy_fills(
        "BTC-USD",
        [{"price": "100000", "size": "9.87984212", "filled_value": "9.87984212"}],
    )
    assert agg.buy_quote_spent == pytest.approx(9.87984212)
    assert agg.buy_base_qty == pytest.approx(9.87984212 / 100_000.0)
    assert agg.buy_base_qty < 0.01


def test_dry_run_old_bug_cannot_reach_flatten_string() -> None:
    """Before/after: old path would sell ~9.88 BTC; normalized path sells ~0.0000988 BTC."""
    r = dry_run_validation_close_from_fixtures(
        "BTC-USD",
        10.0,
        [{"price": "100000", "size": "9.87984212", "filled_value": "9.87984212"}],
        sell_raw_fills=[
            {"price": "100000", "size": "0.00009879", "filled_value": "9.879", "commission": "0.01"}
        ],
    )
    assert r["flatten_validation_error"] is None
    rounded = r["rounded_base_str"]
    assert float(rounded) < 0.01
    assert float(rounded) < 1.0
    assert r["buy_aggregation"]["buy_quote_spent"] == pytest.approx(9.87984212)


def test_validate_flatten_aborts_impossible_base() -> None:
    with pytest.raises(FlattenSizeValidationError):
        validate_flatten_base_before_sell(
            product_id="BTC-USD",
            raw_base_qty_bought=9.87,
            rounded_base_str=round_base_to_increment("BTC-USD", 9.87),
            buy_quote_spent=9.88,
            ref_price_usd_per_base=100_000.0,
            quote_notional_request=10.0,
        )


def test_loss_when_fees_exceed_tiny_gross_move() -> None:
    from trading_ai.global_layer.realized_pnl import compute_realized_pnl

    r = dry_run_validation_close_from_fixtures(
        "BTC-USD",
        10.0,
        [
            {
                "price": "100000",
                "size": "10",
                "filled_value": "10",
                "commission": "0.05",
            }
        ],
        sell_raw_fills=[
            {"price": "100000", "size": "0.0001", "filled_value": "9.98", "commission": "0.05"}
        ],
    )
    pnl = compute_realized_pnl(
        {
            "instrument_kind": "spot",
            "buy_quote_spent": r["buy_aggregation"]["buy_quote_spent"],
            "sell_quote_received": r["sell_quote_received"],
            "fees_total": r["buy_aggregation"]["fees_buy_usd"] + r["fees_sell_usd"],
            "fields_complete": True,
        }
    )
    assert pnl.net_pnl is not None
    assert pnl.gross_pnl is not None
    # Buy ~10, sell ~9.98 → small negative gross; minus fees → loss
    assert pnl.net_pnl < 0


def test_profit_when_sell_exceeds_buy_plus_fees() -> None:
    from trading_ai.global_layer.realized_pnl import compute_realized_pnl

    pnl = compute_realized_pnl(
        {
            "instrument_kind": "spot",
            "buy_quote_spent": 10.0,
            "sell_quote_received": 10.5,
            "fees_total": 0.02,
            "fields_complete": True,
        }
    )
    assert pnl.net_pnl == pytest.approx(0.48)
    assert pnl.pnl_sign == "profit"
