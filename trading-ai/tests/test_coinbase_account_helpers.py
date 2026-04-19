"""Paginated account helpers and live validation quote selection."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from trading_ai.runtime_proof.coinbase_accounts import (
    get_all_coinbase_accounts,
    get_available_quote_balances,
    resolve_validation_market_product,
)
from trading_ai.shark.outlets.coinbase import CoinbaseClient


def _usd_row(val: str) -> dict:
    return {
        "currency": "USD",
        "uuid": "u1",
        "available_balance": {"value": val, "currency": "USD"},
        "balance": {"value": val, "currency": "USD"},
    }


def _ada_row() -> dict:
    return {
        "currency": "ADA",
        "available_balance": {"value": "0.00000022", "currency": "ADA"},
    }


def test_get_available_quote_balances_finds_usd_on_second_logical_page() -> None:
    """Simulate first 'page' without USD/USDC; second page holds USD — helper still sums."""

    class FakeClient:
        def list_all_accounts(self):
            return [_ada_row() for _ in range(3)] + [_usd_row("15.0")]

    b = get_available_quote_balances(FakeClient())  # type: ignore[arg-type]
    assert b["USD"] == 15.0
    assert b["USDC"] == 0.0


def test_resolve_validation_prefers_usd_when_covers_notional() -> None:
    class FakeClient:
        def list_all_accounts(self):
            return [_usd_row("20")]

    pid, diag, err = resolve_validation_market_product(FakeClient(), quote_notional=10.0)  # type: ignore[arg-type]
    assert err is None
    assert pid == "BTC-USD"
    assert diag["quote_balances"]["USD"] == 20.0


def test_resolve_validation_usdc_fallback_when_usd_insufficient() -> None:
    class FakeClient:
        def list_all_accounts(self):
            return [
                _ada_row(),
                {
                    "currency": "USDC",
                    "available_balance": {"value": "25", "currency": "USDC"},
                    "balance": {"value": "25", "currency": "USDC"},
                },
            ]

    with patch(
        "trading_ai.runtime_proof.coinbase_accounts._btc_usdc_spot_tradable",
        return_value=True,
    ):
        pid, diag, err = resolve_validation_market_product(FakeClient(), quote_notional=10.0)  # type: ignore[arg-type]
    assert err is None
    assert pid == "BTC-USDC"
    assert diag["quote_balances"]["USDC"] == 25.0


def test_resolve_validation_fails_when_both_insufficient() -> None:
    class FakeClient:
        def list_all_accounts(self):
            return [_ada_row()]

    pid, diag, err = resolve_validation_market_product(FakeClient(), quote_notional=10.0)  # type: ignore[arg-type]
    assert err == "insufficient_USD_or_USDC_for_notional"
    assert pid == "BTC-USD"
    assert diag["chosen_reason"] == "insufficient_quote"


def test_list_all_accounts_paginates_coinbase_client() -> None:
    """Two /accounts responses merged; proves pagination loop, not single-page."""
    c = CoinbaseClient.__new__(CoinbaseClient)
    c._request = Mock(
        side_effect=[
            {
                "accounts": [_ada_row()],
                "has_next": True,
                "cursor": "nextcur",
            },
            {
                "accounts": [_usd_row("12")],
                "has_next": False,
            },
        ]
    )
    out = CoinbaseClient.list_all_accounts(c)
    assert len(out) == 2
    assert c._request.call_count == 2
    second_call = c._request.call_args_list[1]
    assert second_call[1]["params"].get("cursor") == "nextcur"


def test_get_all_coinbase_accounts_delegates() -> None:
    c = Mock(spec=CoinbaseClient)
    c.list_all_accounts.return_value = [{"currency": "BTC"}]
    rows = get_all_coinbase_accounts(c)
    assert rows == [{"currency": "BTC"}]
    c.list_all_accounts.assert_called_once()
