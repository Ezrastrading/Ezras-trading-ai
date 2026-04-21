"""Hard block: simulation must not run under live-trading env flags."""

from __future__ import annotations

import os

import pytest

from trading_ai.simulation.nonlive import LiveTradingNotAllowedError, assert_nonlive_for_simulation, nonlive_env_ok


def test_nonlive_env_ok_detects_live_mode(monkeypatch) -> None:
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "false")
    ok, why = nonlive_env_ok()
    assert ok is False
    assert why == "live_execution_env_detected"


def test_assert_nonlive_raises_on_coinbase_execution(monkeypatch) -> None:
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    with pytest.raises(LiveTradingNotAllowedError):
        assert_nonlive_for_simulation()
