"""KALSHI_ACTUAL_BALANCE only when API cash is ~0 and Kalshi positions exist."""

from __future__ import annotations

import pytest


def test_should_apply_override_only_zero_api_with_open_kalshi(monkeypatch):
    from trading_ai.shark.kalshi_limits import should_apply_kalshi_actual_balance_override

    monkeypatch.setattr(
        "trading_ai.shark.state_store.load_positions",
        lambda: {"open_positions": []},
    )
    assert should_apply_kalshi_actual_balance_override(0.0) is False
    assert should_apply_kalshi_actual_balance_override(3.25) is False
    assert should_apply_kalshi_actual_balance_override(None) is False

    monkeypatch.setattr(
        "trading_ai.shark.state_store.load_positions",
        lambda: {
            "open_positions": [
                {"outlet": "kalshi", "notional_usd": 10.0},
            ]
        },
    )
    assert should_apply_kalshi_actual_balance_override(0.0) is True
    assert should_apply_kalshi_actual_balance_override(0.01) is False


def test_effective_capital_trusts_positive_api_over_env(monkeypatch):
    from trading_ai.shark.capital_effective import effective_capital_for_outlet

    monkeypatch.setenv("KALSHI_ACTUAL_BALANCE", "24.70")
    monkeypatch.setattr(
        "trading_ai.shark.balance_sync.fetch_kalshi_balance_usd",
        lambda: 2.35,
    )
    assert effective_capital_for_outlet("kalshi", 100.0) == pytest.approx(2.35)


def test_effective_capital_uses_env_when_api_zero_and_positions(monkeypatch):
    from trading_ai.shark.capital_effective import effective_capital_for_outlet

    monkeypatch.setenv("KALSHI_ACTUAL_BALANCE", "24.70")
    monkeypatch.setattr(
        "trading_ai.shark.balance_sync.fetch_kalshi_balance_usd",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        "trading_ai.shark.state_store.load_positions",
        lambda: {"open_positions": [{"outlet": "kalshi", "notional_usd": 5.0}]},
    )
    assert effective_capital_for_outlet("kalshi", 100.0) == pytest.approx(24.70)


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
