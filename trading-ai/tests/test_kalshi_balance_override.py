"""Kalshi treasury: API-led sync with KALSHI_ACTUAL_BALANCE only when API reports $0."""

from __future__ import annotations

import pytest


def test_kalshi_api_reports_zero_balance():
    from trading_ai.shark.balance_sync import kalshi_api_reports_zero_balance

    assert kalshi_api_reports_zero_balance(0.0) is True
    assert kalshi_api_reports_zero_balance(0.01) is False
    assert kalshi_api_reports_zero_balance(1.0) is False


def test_effective_capital_trusts_api_above_trust_min_over_env(monkeypatch):
    from trading_ai.shark.capital_effective import effective_capital_for_outlet

    monkeypatch.setenv("KALSHI_ACTUAL_BALANCE", "99.00")
    monkeypatch.setenv("KALSHI_CASH_RESERVE_PCT", "0")
    monkeypatch.setattr(
        "trading_ai.shark.balance_sync.fetch_kalshi_balance_usd",
        lambda: 50.0,
    )
    assert effective_capital_for_outlet("kalshi", 100.0) == pytest.approx(50.0)


def test_effective_capital_uses_env_when_api_exactly_zero(monkeypatch):
    from trading_ai.shark.capital_effective import effective_capital_for_outlet

    monkeypatch.setenv("KALSHI_ACTUAL_BALANCE", "24.70")
    monkeypatch.setenv("KALSHI_CASH_RESERVE_PCT", "0")
    monkeypatch.setattr(
        "trading_ai.shark.balance_sync.fetch_kalshi_balance_usd",
        lambda: 0.0,
    )
    assert effective_capital_for_outlet("kalshi", 100.0) == pytest.approx(24.70)


def test_effective_capital_small_nonzero_api_not_env(monkeypatch):
    """$0.50 from API is real — do not replace with env when API is not exactly zero."""
    from trading_ai.shark.capital_effective import effective_capital_for_outlet

    monkeypatch.setenv("KALSHI_ACTUAL_BALANCE", "99.00")
    monkeypatch.setenv("KALSHI_CASH_RESERVE_PCT", "0")
    monkeypatch.setattr(
        "trading_ai.shark.balance_sync.fetch_kalshi_balance_usd",
        lambda: 0.50,
    )
    assert effective_capital_for_outlet("kalshi", 100.0) == pytest.approx(0.50)


def test_deployed_usd_sums_kalshi_open(monkeypatch):
    from trading_ai.shark.kalshi_limits import kalshi_open_positions_deployed_usd

    monkeypatch.setattr(
        "trading_ai.shark.state_store.load_positions",
        lambda: {
            "open_positions": [
                {"outlet": "kalshi", "notional_usd": 3.0},
                {"outlet": "kalshi", "notional_usd": 2.5},
                {"outlet": "polymarket", "notional_usd": 99.0},
            ]
        },
    )
    assert kalshi_open_positions_deployed_usd() == pytest.approx(5.5)
