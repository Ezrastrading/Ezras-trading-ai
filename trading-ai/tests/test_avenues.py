"""Multi-avenue + sports + options tests — gates 81–90."""

from __future__ import annotations

import argparse
import json

import pytest


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    yield


# ── Test 81 ──────────────────────────────────────────────────────────────────

def test_81_avenue_registry_has_nine_avenues():
    from trading_ai.shark.avenues import load_avenues

    avenues = load_avenues()
    assert set(avenues.keys()) == {
        "kalshi",
        "manifold",
        "polymarket",
        "metaculus",
        "coinbase",
        "robinhood",
        "tastytrade",
        "webull",
        "sports_manual",
    }
    for name, av in avenues.items():
        assert av.starting_capital == 25.0, f"{name} starting_capital should be 25.00"
        assert av.current_capital == 25.0, f"{name} current_capital should equal starting on init"
        assert av.automation_level in ("full", "semi", "manual_only")
    assert avenues["sports_manual"].automation_level == "manual_only"


# ── Test 82 ──────────────────────────────────────────────────────────────────

def test_82_avenue_state_persists_and_loads():
    from trading_ai.shark.avenues import load_avenues, record_trade_result, save_avenues

    avenues = load_avenues()
    avenues["kalshi"].current_capital = 42.50
    save_avenues(avenues)

    loaded = load_avenues()
    assert loaded["kalshi"].current_capital == 42.50

    # record_trade_result persists too
    record_trade_result("manifold", pnl=5.00, win=True)
    reloaded = load_avenues()
    assert reloaded["manifold"].total_trades == 1
    assert reloaded["manifold"].total_profit == pytest.approx(5.00, abs=0.01)
    assert reloaded["manifold"].win_rate == pytest.approx(1.0, abs=0.01)


# ── Test 83 ──────────────────────────────────────────────────────────────────

def test_83_dashboard_aggregates_total_capital():
    from trading_ai.shark.dashboard import get_master_dashboard

    dash = get_master_dashboard()

    # 9 avenues × $25 = $225 total deployed
    assert dash["total_capital_deployed"] == pytest.approx(225.0, abs=0.01)
    assert dash["total_current_value"] == pytest.approx(225.0, abs=0.01)
    assert set(dash["avenues"].keys()) == {
        "kalshi",
        "manifold",
        "polymarket",
        "metaculus",
        "coinbase",
        "robinhood",
        "tastytrade",
        "webull",
        "sports_manual",
    }
    assert "treasury" in dash
    assert "month_4_projection" in dash
    assert "year_end_projection" in dash
    # Month 4 projections across all avenues should exceed $40k
    assert dash["month_4_projection"] > 40_000


# ── Test 84 ──────────────────────────────────────────────────────────────────

def test_84_sports_bet_analysis_uses_kelly_sizing():
    from trading_ai.shark.sports_tracker import analyze_sports_bet

    # +150 odds, 55% estimated probability → clear positive edge
    result = analyze_sports_bet(
        event="NYK vs BOS",
        bet_type="Moneyline NYK",
        american_odds=150,
        estimated_probability=0.55,
        bankroll=100.0,
    )
    assert result["edge"] > 0, "Expected positive edge"
    assert result["kelly_fraction"] > 0, "Expected positive Kelly fraction"
    assert result["recommended_usd"] > 0, "Expected non-zero recommended bet"
    assert result["recommended_usd"] <= 100.0 * 0.10, "Kelly capped at 10% of bankroll"
    assert result["actionable"] is True

    # Negative edge → no bet
    result_bad = analyze_sports_bet(
        event="NYK vs BOS",
        bet_type="Moneyline NYK",
        american_odds=-200,
        estimated_probability=0.40,   # market implies 0.667
        bankroll=100.0,
    )
    assert result_bad["edge"] < 0
    assert result_bad["kelly_fraction"] == 0.0
    assert result_bad["recommended_usd"] == 0.0
    assert result_bad["actionable"] is False


# ── Test 85 ──────────────────────────────────────────────────────────────────

def test_85_sports_picks_ny_compliant_no_execution(monkeypatch):
    """
    NY law compliance: get_daily_picks must NOT make API calls
    to FanDuel/DraftKings or any betting platform.
    """
    from trading_ai.shark.sports_tracker import (
        NY_AUTOMATED_BETTING_PROHIBITED,
        get_daily_picks,
    )

    # Confirm the compliance flag exists
    assert NY_AUTOMATED_BETTING_PROHIBITED is True

    # Patch urllib to detect any outbound calls to betting sites
    blocked_urls: list = []

    original_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen

    def mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for domain in ("fanduel", "draftkings", "betmgm", "caesars"):
            if domain in url.lower():
                blocked_urls.append(url)
                raise RuntimeError(f"Blocked automated call to betting platform: {url}")
        return original_urlopen(req, *args, **kwargs)

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # Should return picks without ever touching a betting platform
    picks = get_daily_picks(bankroll=25.0)
    assert isinstance(picks, list), "get_daily_picks should return a list"
    assert len(blocked_urls) == 0, f"Made illegal API calls: {blocked_urls}"


# ── Test 86 ──────────────────────────────────────────────────────────────────

def test_86_sports_result_logging_updates_avenue_pnl(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "shark" / "state").mkdir(parents=True, exist_ok=True)
    from trading_ai.shark.avenues import load_avenues
    from trading_ai.shark.sports_tracker import log_sports_result

    # Log a win
    log_sports_result(event_id="nyk-bos-g1", outcome="win", amount=10.0, pnl=15.0)
    avenues = load_avenues()
    assert avenues["sports_manual"].total_trades == 1
    assert avenues["sports_manual"].total_profit == pytest.approx(15.0, abs=0.01)
    assert avenues["sports_manual"].win_rate == pytest.approx(1.0)

    # Log a loss
    log_sports_result(event_id="nyk-bos-g2", outcome="loss", amount=10.0, pnl=-10.0)
    avenues = load_avenues()
    assert avenues["sports_manual"].total_trades == 2
    assert avenues["sports_manual"].total_profit == pytest.approx(5.0, abs=0.01)
    assert avenues["sports_manual"].win_rate == pytest.approx(0.5, abs=0.01)


# ── Test 87 ──────────────────────────────────────────────────────────────────

def test_87_tastytrade_enforces_40pct_position_limit():
    from trading_ai.shark.outlets.tastytrade import TastytradeClient

    client = TastytradeClient()

    # Under 40% — should pass
    assert client.validate_defined_risk("C", account_balance=100.0, stake_usd=39.0) is True
    assert client.validate_defined_risk("P", account_balance=100.0, stake_usd=40.0) is True

    # Over 40% — should reject
    assert client.validate_defined_risk("C", account_balance=100.0, stake_usd=41.0) is False

    # Non-long option type — should reject
    assert client.validate_defined_risk("SELL_CALL", account_balance=100.0, stake_usd=10.0) is False
    assert client.validate_defined_risk("NAKED_PUT", account_balance=100.0, stake_usd=5.0) is False


# ── Test 88 ──────────────────────────────────────────────────────────────────

def test_88_tastytrade_handles_missing_credentials(monkeypatch):
    monkeypatch.delenv("TASTYTRADE_USERNAME", raising=False)
    monkeypatch.delenv("TASTYTRADE_PASSWORD", raising=False)

    from trading_ai.shark.outlets.tastytrade import TastytradeClient

    client = TastytradeClient()
    assert client.has_credentials() is False
    assert client.authenticate() is False
    assert client.get_account_balance() is None
    assert client.get_positions() == []

    order_result = client.place_order(
        symbol="SPY", option_type="C", strike=450.0,
        expiry="2025-12-19", quantity=1, price=2.50,
        account_balance=100.0,
    )
    assert order_result["ok"] is False


# ── Test 89 ──────────────────────────────────────────────────────────────────

def test_89_webull_handles_missing_credentials(monkeypatch):
    monkeypatch.delenv("WEBULL_API_KEY", raising=False)
    monkeypatch.delenv("WEBULL_ACCOUNT_ID", raising=False)

    from trading_ai.shark.outlets.webull import WebullClient

    client = WebullClient()
    assert client.has_credentials() is False
    assert client.get_account_balance() is None
    assert client.get_positions() == []

    order_result = client.place_order(
        ticker="SPY", option_side="BUY_CALL", strike=450.0,
        expiry="2025-12-19", quantity=1, limit_price=2.50,
        account_balance=100.0,
    )
    assert order_result["ok"] is False


# ── Test 90 ──────────────────────────────────────────────────────────────────

def test_90_growth_projection_month4_target():
    from trading_ai.shark.growth_tracker import MONTHLY_TARGETS, get_growth_status

    # Month 4 starts at $35,000, target is $120,000 (MINIMUM)
    month4_start, month4_target = MONTHLY_TARGETS[3]
    assert month4_start == pytest.approx(35_000.0)
    assert month4_target == pytest.approx(120_000.0)

    # At $35,000 capital, month_index should be 4
    from trading_ai.shark.growth_tracker import current_month_index
    assert current_month_index(35_000.0) == 4

    # Halfway through month 4, at $77,500 (halfway to $120,000) → on_pace
    # needed=85000, achieved=42500, progress=50%; expected=50% → on_pace
    status = get_growth_status(77_500.0, month_start_capital=35_000.0, days_elapsed=15)
    assert status["month_index"] == 4
    assert status["monthly_target"] == 120_000.0
    assert status["on_pace"] is True

    # Behind: only $45,000 at day 20 (67% elapsed, ~12% progress)
    status_behind = get_growth_status(45_000.0, month_start_capital=35_000.0, days_elapsed=20)
    assert status_behind["trajectory"] in ("behind", "critical")
