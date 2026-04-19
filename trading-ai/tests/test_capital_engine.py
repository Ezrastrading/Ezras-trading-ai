"""Tests for account-level sizing and capital preflight."""

from datetime import datetime, timezone

from trading_ai.core.capital_engine import CapitalEngine, CapitalLimits, capital_preflight_block


def _today_utc_str() -> str:
    return str(datetime.now(timezone.utc).date())


def test_get_trade_size_default_two_percent_floor_and_cap():
    lim = CapitalLimits()
    assert CapitalEngine.get_trade_size(100.0, lim) == 10.0  # min
    assert abs(CapitalEngine.get_trade_size(1_000.0, lim) - 20.0) < 1e-9  # 2%
    assert abs(CapitalEngine.get_trade_size(10_000.0, lim) - 200.0) < 1e-9


def test_get_trade_size_max_ten_percent():
    lim = CapitalLimits()
    assert abs(CapitalEngine.get_trade_size(500.0, lim) - 10.0) < 1e-9  # min dominates
    out = CapitalEngine.get_trade_size(50_000.0, lim)
    assert abs(out - 1_000.0) < 1e-9  # min(1000 2%, 5000 10%) = 1000


def test_enforce_max_per_trade():
    ce = CapitalEngine(current_balance=1_000.0, open_exposure=0.0, daily_pnl=0.0)
    ce.day_utc = _today_utc_str()
    ce.day_start_balance = 1_000.0
    ok, reason = ce.enforce_limits(proposed_trade_usd=150.0, account_balance_usd=1_000.0)
    assert not ok
    assert reason == "max_per_trade"


def test_enforce_max_total_exposure():
    ce = CapitalEngine(current_balance=1_000.0, open_exposure=250.0, daily_pnl=0.0)
    ce.day_utc = _today_utc_str()
    ce.day_start_balance = 1_000.0
    ok, reason = ce.enforce_limits(proposed_trade_usd=60.0, account_balance_usd=1_000.0)
    assert not ok
    assert reason == "max_total_exposure"


def test_daily_drawdown_stop():
    ce = CapitalEngine(current_balance=900.0, open_exposure=0.0, daily_pnl=-110.0)
    ce.day_utc = _today_utc_str()
    ce.day_start_balance = 1_000.0
    ok, reason = ce.enforce_limits(proposed_trade_usd=1.0, account_balance_usd=1_000.0)
    assert not ok
    assert reason == "max_drawdown_stop"


def test_capital_preflight_block_helper():
    blocked, reason = capital_preflight_block(
        proposed_trade_usd=8.0,
        account_balance_usd=100.0,
        open_exposure_usd=0.0,
        daily_pnl_usd=0.0,
        day_start_balance_usd=100.0,
    )
    assert not blocked
    assert reason is None

    blocked2, r2 = capital_preflight_block(
        proposed_trade_usd=15.0,
        account_balance_usd=100.0,
        open_exposure_usd=0.0,
        daily_pnl_usd=0.0,
        day_start_balance_usd=100.0,
    )
    assert blocked2
    assert r2 == "max_per_trade"
