"""Beast architecture — Metaculus, Coinbase outlet, avenue activator, master wallet, hunts."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trading_ai.shark.models import HuntType, MarketSnapshot
from trading_ai.shark.scan_execute import attach_metaculus_reference_prices


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    yield


def test_metaculus_maps_question_to_snapshot():
    from trading_ai.shark.outlets.metaculus import map_metaculus_question_to_snapshot

    now = 1_700_000_000.0
    row = {
        "id": 99,
        "title": "Will GDP grow?",
        "resolution_criteria": "Resolves per BEA.",
        "community_prediction": {"full": {"q2": 0.62}},
        "scheduled_close_time": now + 86400 * 3,
    }
    m = map_metaculus_question_to_snapshot(row, now)
    assert m is not None
    assert m.outlet == "metaculus"
    assert m.market_id == "metaculus:99"
    assert m.yes_price == pytest.approx(0.62, abs=0.01)


def test_metaculus_fetcher_parses_results(monkeypatch):
    monkeypatch.setenv("METACULUS_API_TOKEN", "test-token")
    from trading_ai.shark.outlets.metaculus import MetaculusFetcher

    payload = {
        "results": [
            {
                "id": 1,
                "title": "Test Q",
                "community_prediction": {"full": {"q2": 0.4}},
                "scheduled_close_time": 1_800_000_000.0,
            }
        ]
    }

    def fake_get(url, headers=None):
        _ = url, headers
        return payload

    monkeypatch.setattr("trading_ai.shark.outlets.metaculus._http_get_json", fake_get)
    rows = MetaculusFetcher().fetch_binary_markets()
    assert len(rows) == 1
    assert rows[0].yes_price == pytest.approx(0.4, abs=0.01)


def test_attach_metaculus_reference_prices_on_kalshi():
    now = 1_700_000_000.0
    k = MarketSnapshot(
        market_id="KX-TEST",
        outlet="kalshi",
        yes_price=0.5,
        no_price=0.5,
        volume_24h=1000.0,
        time_to_resolution_seconds=3600.0,
        resolution_criteria="Fed raises rates",
        last_price_update_timestamp=now,
        question_text="Fed raises rates",
    )
    mc = MarketSnapshot(
        market_id="metaculus:1",
        outlet="metaculus",
        yes_price=0.72,
        no_price=0.28,
        volume_24h=10.0,
        time_to_resolution_seconds=86400.0,
        resolution_criteria="Fed raises rates",
        last_price_update_timestamp=now,
        question_text="Fed raises rates",
    )
    attach_metaculus_reference_prices([k, mc])
    u = k.underlying_data_if_available or {}
    assert u.get("metaculus_yes_reference") == pytest.approx(0.72, abs=0.01)


def test_hunt_kalshi_metaculus_divergence():
    from trading_ai.shark.kalshi_hunts import hunt_kalshi_metaculus_divergence

    now = 1_700_000_000.0
    m = MarketSnapshot(
        market_id="KX-1",
        outlet="kalshi",
        yes_price=0.4,
        no_price=0.6,
        volume_24h=2000.0,
        time_to_resolution_seconds=7200.0,
        resolution_criteria="x",
        last_price_update_timestamp=now,
        underlying_data_if_available={"metaculus_yes_reference": 0.75},
    )
    h = hunt_kalshi_metaculus_divergence(m)
    assert h is not None
    assert h.hunt_type == HuntType.KALSHI_METACULUS_DIVERGE


def test_coinbase_fetch_crypto_prices_mocked():
    from trading_ai.shark.outlets import coinbase as cb_mod

    def fake_spot(pid):
        return {"BTC-USD": 42_000.0, "ETH-USD": 2200.0}.get(pid)

    with patch.object(cb_mod, "_public_spot", side_effect=fake_spot):
        from trading_ai.shark.outlets.coinbase import CoinbaseFetcher

        px = CoinbaseFetcher.fetch_crypto_prices()
        assert px["BTC-USD"] == 42_000.0
        assert px["ETH-USD"] == 2200.0


def test_default_fetchers_include_metaculus():
    from trading_ai.shark.outlets import default_fetchers

    names = {f.outlet_name for f in default_fetchers()}
    assert "metaculus" in names


def test_avenue_activator_evaluate_smoke():
    from trading_ai.shark.avenue_activator import evaluate_avenues

    rows = evaluate_avenues()
    assert len(rows) >= 6
    assert {r.key for r in rows} >= {"kalshi", "metaculus", "coinbase"}


def test_master_wallet_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.master_wallet import load_master_wallet, save_master_wallet

    w = load_master_wallet()
    assert "by_avenue" in w
    w["month_target"] = 2000.0
    save_master_wallet(w)
    w2 = load_master_wallet()
    assert w2["month_target"] == 2000.0


def test_run_filtered_includes_metaculus_hunts():
    from trading_ai.shark.crypto_polymarket_hunts import run_filtered_polymarket_hunts

    now = 1_700_000_000.0
    m = MarketSnapshot(
        market_id="KX-1",
        outlet="kalshi",
        yes_price=0.3,
        no_price=0.7,
        volume_24h=2000.0,
        time_to_resolution_seconds=7200.0,
        resolution_criteria="x",
        last_price_update_timestamp=now,
        underlying_data_if_available={"metaculus_yes_reference": 0.8},
    )
    hs = run_filtered_polymarket_hunts(
        m,
        {HuntType.KALSHI_METACULUS_DIVERGE},
    )
    assert any(h.hunt_type == HuntType.KALSHI_METACULUS_DIVERGE for h in hs)
