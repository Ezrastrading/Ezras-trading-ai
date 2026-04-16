"""Kalshi simple scanner — Gate A/B filtering and ask-based side selection."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
def _env_gate_a(monkeypatch):
    monkeypatch.setenv("KALSHI_GATE_A_ENABLED", "true")
    monkeypatch.setenv("KALSHI_GATE_B_ENABLED", "false")
    yield


def test_gate_a_series_defaults():
    import trading_ai.shark.kalshi_simple_scanner as ks

    assert "KXBTCD" in ks._gate_a_series_tickers()
    assert "KXETHD" in ks._gate_a_series_tickers()


def test_filter_gate_b_skips_far_close():
    from trading_ai.shark.kalshi_simple_scanner import _filter_simple_candidates

    far = time.time() + 86400 * 30
    cands = [
        {
            "ticker": "KXFOO-TEST",
            "prob": 0.92,
            "side": "yes",
            "yes_bid": 0.9,
            "no_bid": 0.08,
            "close_ts": far,
        }
    ]
    out = _filter_simple_candidates(cands, time.time(), gate="b")
    assert out == []


def test_filter_gate_a_skips_wrong_calendar_day(_env_gate_a):
    from trading_ai.shark.kalshi_simple_scanner import _filter_simple_candidates

    tomorrow = time.time() + 90000  # > 1 day
    cands = [
        {
            "ticker": "KXBTCD-TEST-T95000",
            "prob": 0.92,
            "side": "yes",
            "yes_bid": 0.9,
            "no_bid": 0.08,
            "close_ts": tomorrow,
        }
    ]
    import trading_ai.shark.kalshi_simple_scanner as ks_mod

    with patch.object(ks_mod, "_fetch_btc_eth_spot", return_value=(95000.0, 3000.0)), patch.object(
        ks_mod,
        "_kalshi_crypto_market_hours_ok",
        return_value=True,
    ):
        out = _filter_simple_candidates(cands, time.time(), gate="a")
    assert out == []


def test_filter_accepts_gate_a_same_day_in_range(_env_gate_a):
    from trading_ai.shark import kalshi_simple_scanner as ks_mod
    from trading_ai.shark.kalshi_simple_scanner import _filter_simple_candidates

    # Close time strictly before UTC midnight on the same calendar day as "now".
    now_dt = datetime.now(timezone.utc)
    eod = now_dt.replace(hour=23, minute=59, second=50, microsecond=0)
    target = min(now_dt + timedelta(minutes=30), eod)
    if target <= now_dt:
        target = now_dt + timedelta(seconds=5)
    close_ts = target.timestamp()
    cands = [
        {
            "ticker": "KXBTCD-TEST-T95000",
            "prob": 0.45,
            "side": "yes",
            "price": 0.45,
            "yes_bid": 0.4,
            "no_bid": 0.55,
            "close_ts": close_ts,
        }
    ]
    with patch.object(ks_mod, "_fetch_btc_eth_spot", return_value=(95000.0, 3000.0)), patch.object(
        ks_mod,
        "_fetch_spx_spot",
        return_value=None,
    ), patch.object(ks_mod, "_kalshi_crypto_market_hours_ok", return_value=True):
        out = _filter_simple_candidates(cands, time.time(), gate="a")
    assert len(out) == 1
    assert out[0]["ticker"].startswith("KXBTCD")


def test_kalshi_yes_no_ask_from_row():
    from trading_ai.shark.outlets.kalshi import _kalshi_yes_no_ask_from_market_row

    row = {"yes_ask_dollars": "0.91", "no_ask_dollars": "0.10"}
    ya, na, _, _ = _kalshi_yes_no_ask_from_market_row(row)
    assert ya == pytest.approx(0.91)
    assert na == pytest.approx(0.10)


def test_pick_side_prefers_higher_roi_when_both_qualify():
    from trading_ai.shark.kalshi_simple_scanner import _pick_kalshi_side_by_prob_roi

    side, px, prob, roi = _pick_kalshi_side_by_prob_roi(0.40, 0.50, 0.35, 20.0)
    assert side == "yes"
    assert px == pytest.approx(0.40)
    assert roi > 100.0


def test_gate_a_defaults_min_roi_150():
    import trading_ai.shark.kalshi_simple_scanner as ks

    assert ks._gate_min_roi_pct("a") == pytest.approx(150.0)


def test_gate_b_defaults_min_roi_20():
    import trading_ai.shark.kalshi_simple_scanner as ks

    assert ks._gate_min_roi_pct("b") == pytest.approx(20.0)


def test_contract_bounds_gate_a_wider_than_legacy():
    import trading_ai.shark.kalshi_simple_scanner as ks

    lo_a, hi_a = ks._contract_cost_bounds("a")
    assert lo_a == pytest.approx(0.01)
    assert hi_a == pytest.approx(0.80)
    lo_l, hi_l = ks._contract_cost_bounds("legacy")
    assert lo_l == pytest.approx(0.35)
    assert hi_l == pytest.approx(0.65)
