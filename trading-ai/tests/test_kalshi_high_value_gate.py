"""Tests for Kalshi high-value long-shot gate helpers."""

from __future__ import annotations

import pytest


def test_obvious_no_index_outside_range() -> None:
    from trading_ai.shark.kalshi_high_value_gate import _obvious_no_far_from_strike

    inner = {"title": "S&P 500 closes 6975-6999"}
    assert _obvious_no_far_from_strike(
        "KXSPX-TEST",
        inner,
        spx=5500.0,
        btc=None,
        eth=None,
    )


def test_obvious_no_rejects_inside_range() -> None:
    from trading_ai.shark.kalshi_high_value_gate import _obvious_no_far_from_strike

    inner = {"title": "S&P 500 closes 6975-6999"}
    assert not _obvious_no_far_from_strike(
        "KXSPX-TEST",
        inner,
        spx=6980.0,
        btc=None,
        eth=None,
    )


def test_find_high_value_trades_filters_ttr(monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.shark import kalshi_high_value_gate as hv

    monkeypatch.setenv("KALSHI_HV_MAX_TRADES_DAY", "3")
    monkeypatch.setenv("KALSHI_HV_ALLOC_PCT", "0.15")
    monkeypatch.setenv("KALSHI_HV_MAX_PER_TRADE", "5")

    class FakeClient:
        def fetch_markets_for_series(self, ser: str, limit: int = 80):
            return []

        def enrich_market_with_detail_and_orderbook(self, m):
            return m

        def has_kalshi_credentials(self):
            return True

    monkeypatch.setattr(hv, "_available_deployable_usd", lambda: 100.0)
    monkeypatch.setattr(hv, "_fetch_spx_spot", lambda: 5500.0)
    monkeypatch.setattr(hv, "_fetch_btc_eth", lambda: (None, None))

    out = hv.find_high_value_trades(FakeClient())
    assert out == []
