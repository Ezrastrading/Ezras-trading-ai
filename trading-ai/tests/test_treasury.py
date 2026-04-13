"""Treasury tracking tests — gates 76–80."""

from __future__ import annotations

import argparse
import json

import pytest


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    # Clear any cached treasury state between tests
    yield


# ── Test 76 ─────────────────────────────────────────────────────────────────

def test_76_treasury_initializes_at_10():
    from trading_ai.shark.treasury import load_treasury

    state = load_treasury()
    assert state["total_deposited_usd"] == 10.00
    assert state["net_worth_usd"] == 10.00
    assert state["kalshi_balance_usd"] == 10.00
    assert state["manifold_balance_usd"] == 0.00
    assert state["withdrawal_history"] == []
    assert "last_updated" in state


# ── Test 77 ─────────────────────────────────────────────────────────────────

def test_77_balance_update_recalculates_net_worth():
    from trading_ai.shark.treasury import load_treasury, update_platform_balances

    update_platform_balances(kalshi_usd=35.50, manifold_usd=5.00)
    state = load_treasury()

    assert state["kalshi_balance_usd"] == 35.50
    assert state["manifold_balance_usd"] == 5.00
    assert state["net_worth_usd"] == 40.50
    # profit = net_worth - deposited + withdrawn = 40.50 - 10.00 + 0 = 30.50
    assert state["total_profit_usd"] == pytest.approx(30.50, abs=0.01)


# ── Test 78 ─────────────────────────────────────────────────────────────────

def test_78_withdrawal_alert_fires_at_threshold(monkeypatch):
    from trading_ai.shark.treasury import load_treasury, save_treasury, update_platform_balances

    # Lower the threshold so we can test without large numbers
    state = load_treasury()
    state["withdrawal_alert_threshold"] = 100.0
    save_treasury(state)

    alerts: list = []
    monkeypatch.setattr(
        "trading_ai.shark.reporting.send_telegram",
        lambda msg: alerts.append(msg) or True,
    )

    # Below threshold — no alert
    update_platform_balances(50.0, 0.0)
    assert len(alerts) == 0

    # Above threshold — alert fires
    update_platform_balances(150.0, 0.0)
    assert len(alerts) == 1
    assert "WITHDRAWAL ALERT" in alerts[0]
    assert "$150.00" in alerts[0]


# ── Test 79 ─────────────────────────────────────────────────────────────────

def test_79_growth_tracker_trajectory():
    from trading_ai.shark.growth_tracker import MONTHLY_TARGETS, get_growth_status

    # No progress halfway through Month 1 → critical
    # Month 1: $25 → $1,750 (MINIMUM). Zero gain at day 15 = critical.
    status = get_growth_status(25.0, month_start_capital=25.0, days_elapsed=15)
    assert status["trajectory"] == "critical"
    assert status["on_pace"] is False
    assert status["monthly_target"] == MONTHLY_TARGETS[0][1]  # 1_750.0
    assert "projected_month_end" in status

    # Well ahead at $1,200 halfway through Month 1 → ahead
    # needed=1725, achieved=1175, progress=68.1%; expected=50%*1.1=55% → ahead
    status2 = get_growth_status(1200.0, month_start_capital=25.0, days_elapsed=15)
    assert status2["trajectory"] == "ahead"
    assert status2["on_pace"] is True

    # Right on pace: 50% of month elapsed, 50% of target achieved
    # needed=1725, halfway=887.5, current=25+862.5=887.5 → on_pace
    status3 = get_growth_status(887.5, month_start_capital=25.0, days_elapsed=15)
    # progress=50%, expected=50% → on_pace
    assert status3["trajectory"] in ("on_pace", "ahead")


# ── Test 80 ─────────────────────────────────────────────────────────────────

def test_80_cli_treasury_returns_expected_structure(capsys):
    from trading_ai.shark.cli import cmd_treasury

    cmd_treasury(argparse.Namespace(action="", amount=None))
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    for key in ("net_worth_usd", "kalshi_balance_usd", "manifold_balance_usd",
                "total_deposited_usd", "all_time_profit_usd", "return_on_investment_pct"):
        assert key in data, f"missing key: {key}"

    assert isinstance(data["net_worth_usd"], (int, float))
    assert isinstance(data["withdrawal_history"], list)
