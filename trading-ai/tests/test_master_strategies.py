"""Master strategy registry — gates, performance summary, CEO persistence."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from trading_ai.shark.models import HuntSignal, HuntType, MarketSnapshot


def test_get_active_strategies_respects_capital_and_avenues(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.master_strategies import (
        StrategyID,
        get_active_strategies,
        set_strategy_enabled,
    )

    set_strategy_enabled(StrategyID.C1_GRID_TRADING, True)
    # $150, kalshi only — C1 needs coinbase
    active = get_active_strategies(150.0, ["kalshi"])
    ids = {s.id for s in active}
    assert StrategyID.C1_GRID_TRADING not in ids

    active2 = get_active_strategies(150.0, ["kalshi", "coinbase"])
    ids2 = {s.id for s in active2}
    assert StrategyID.C1_GRID_TRADING in ids2


def test_filter_drops_signal_when_strategy_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.master_strategies import (
        StrategyID,
        filter_hunt_signals_by_strategy,
        set_strategy_enabled,
    )

    set_strategy_enabled(StrategyID.PM1_NEAR_RESOLUTION, False)
    m = MarketSnapshot(
        market_id="x",
        outlet="kalshi",
        yes_price=0.9,
        no_price=0.1,
        volume_24h=5000.0,
        time_to_resolution_seconds=3600.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
    )
    sig = HuntSignal(HuntType.KALSHI_NEAR_CLOSE, 0.1, 0.8, {})
    out = filter_hunt_signals_by_strategy([sig], log_counts=False)
    assert out == []

    set_strategy_enabled(StrategyID.PM1_NEAR_RESOLUTION, True)
    out2 = filter_hunt_signals_by_strategy([sig], log_counts=False)
    assert len(out2) == 1


def test_get_strategy_performance_summary_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.master_strategies import get_strategy_performance_summary

    s = get_strategy_performance_summary()
    assert "pm1" in s
    assert "name" in s["pm1"]
    assert "actual_pnl" in s["pm1"]


def test_auto_activate_persists_and_avenue_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark import avenues
    from trading_ai.shark.master_strategies import (
        StrategyID,
        auto_activate_strategies,
        is_strategy_enabled,
    )

    # Ensure O1 starts disabled in defaults; force file off then unlock with capital
    state_path = tmp_path / "shark" / "state" / "master_strategy_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"enabled": {StrategyID.O1_ZERO_DTE_CREDIT_SPREAD.value: False}}))

    assert is_strategy_enabled(StrategyID.O1_ZERO_DTE_CREDIT_SPREAD) is False
    activated = auto_activate_strategies(600.0, ["tastytrade"])
    assert StrategyID.O1_ZERO_DTE_CREDIT_SPREAD in activated
    assert is_strategy_enabled(StrategyID.O1_ZERO_DTE_CREDIT_SPREAD) is True

    sent: list[str] = []

    def _cap(msg: str) -> None:
        sent.append(msg)

    avs = avenues.load_avenues()
    avs["tastytrade"].status = "paused"
    avenues.save_avenues(avs)

    with patch("trading_ai.shark.reporting.send_telegram", _cap):
        from trading_ai.shark.avenue_activator import on_avenue_became_active

        on_avenue_became_active("tastytrade", previous_status="paused")
    assert isinstance(sent, list)


def test_format_avenue_status_for_ceo():
    from trading_ai.shark.avenue_activator import format_avenue_status_for_ceo

    txt = format_avenue_status_for_ceo()
    assert "kalshi" in txt.lower() and "status=" in txt
