"""Unified realized PnL and expectancy helpers."""

from __future__ import annotations

import pytest

from trading_ai.global_layer.realized_pnl import compute_expectancy, compute_realized_pnl


def test_spot_profit_loss_flat() -> None:
    p = compute_realized_pnl(
        {
            "instrument_kind": "spot",
            "buy_quote_spent": 100.0,
            "sell_quote_received": 101.0,
            "fees_total": 0.5,
            "fields_complete": True,
        }
    )
    assert p.net_pnl == pytest.approx(0.5)
    assert p.pnl_sign == "profit"

    p2 = compute_realized_pnl(
        {
            "instrument_kind": "spot",
            "buy_quote_spent": 100.0,
            "sell_quote_received": 99.0,
            "fees_total": 0.5,
            "fields_complete": True,
        }
    )
    assert p2.pnl_sign == "loss"

    p3 = compute_realized_pnl(
        {
            "instrument_kind": "spot",
            "buy_quote_spent": 10.0,
            "sell_quote_received": 10.0,
            "fees_total": 0.0,
            "fields_complete": True,
        }
    )
    assert p3.pnl_sign == "flat"


def test_spot_unknown_when_incomplete() -> None:
    p = compute_realized_pnl(
        {
            "instrument_kind": "spot",
            "buy_quote_spent": 10.0,
            "sell_quote_received": None,
            "fees_total": None,
            "fields_complete": False,
        }
    )
    assert p.net_pnl is None
    assert p.pnl_sign == "unknown"


def test_prediction_net_pnl() -> None:
    p = compute_realized_pnl(
        {
            "instrument_kind": "prediction",
            "contracts": 10.0,
            "entry_price": 0.4,
            "payout": 1.0,
            "fees_total": 0.1,
        }
    )
    assert p.net_pnl is not None
    assert p.net_pnl == pytest.approx(10.0 * 1.0 - 10.0 * 0.4 - 0.1)


def test_options_net_pnl() -> None:
    p = compute_realized_pnl(
        {
            "instrument_kind": "options",
            "contracts": 2.0,
            "multiplier": 100.0,
            "entry_value": 1.5,
            "exit_value": 2.0,
            "fees_total": 1.0,
        }
    )
    assert p.net_pnl == pytest.approx((2.0 - 1.5) * 2.0 * 100.0 - 1.0)


def test_expectancy_matches_known_inputs() -> None:
    e = compute_expectancy(win_rate=0.5, avg_win=20.0, loss_rate=0.5, avg_loss=8.0)
    assert e == pytest.approx(6.0)
