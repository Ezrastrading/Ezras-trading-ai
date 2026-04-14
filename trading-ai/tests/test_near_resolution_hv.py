"""Near-resolution HV hunt, sizing hooks, and capital daily limits."""

from __future__ import annotations

import pytest

from trading_ai.shark.models import HuntType, MarketSnapshot, OpportunityTier, ScoredOpportunity
from trading_ai.shark.models import HuntSignal


def _snap(yes: float, no: float, *, outlet: str = "kalshi") -> MarketSnapshot:
    return MarketSnapshot(
        market_id="TEST-HV",
        outlet=outlet,
        yes_price=yes,
        no_price=no,
        volume_24h=5000.0,
        time_to_resolution_seconds=3600.0,
        resolution_criteria="test market",
        last_price_update_timestamp=0.0,
        market_category="sports",
    )


def test_hunt_near_resolution_hv_tier1_yes_97():
    from trading_ai.shark.kalshi_hunts import hunt_near_resolution_hv

    s = hunt_near_resolution_hv(_snap(0.97, 0.03))
    assert s is not None
    assert s.hunt_type == HuntType.NEAR_RESOLUTION_HV
    assert s.details.get("stake_fraction") == pytest.approx(0.75, abs=0.001)
    assert s.details.get("side") == "yes"


def test_hunt_near_resolution_hv_tier3_yes_90():
    from trading_ai.shark.kalshi_hunts import hunt_near_resolution_hv

    s = hunt_near_resolution_hv(_snap(0.91, 0.09))
    assert s is not None
    assert s.details.get("stake_fraction") == pytest.approx(0.30, abs=0.001)
    assert s.details.get("tier") == "T3"


def test_hunt_near_resolution_hv_no_signal_below_90():
    from trading_ai.shark.kalshi_hunts import hunt_near_resolution_hv

    assert hunt_near_resolution_hv(_snap(0.88, 0.12)) is None


def test_hv_metaculus_boost_caps_stake():
    from trading_ai.shark.kalshi_hunts import hunt_near_resolution_hv

    m = _snap(0.97, 0.03)
    m.underlying_data_if_available = {"metaculus_yes_reference": 0.96}
    s = hunt_near_resolution_hv(m)
    assert s is not None
    assert s.details.get("stake_fraction") == pytest.approx(min(0.80, 0.75 * 1.25), abs=0.01)


def test_get_daily_trade_limit_for_capital():
    from trading_ai.shark.state_store import get_daily_trade_limit_for_capital

    assert get_daily_trade_limit_for_capital(50) == 10
    assert get_daily_trade_limit_for_capital(150) == 20
    assert get_daily_trade_limit_for_capital(800) == 35
    assert get_daily_trade_limit_for_capital(5000) == 50


def test_build_execution_intent_hv_sizes(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark import executor
    from trading_ai.shark.state_store import save_positions

    save_positions({"open_positions": [], "pending_resolution": [], "history": []})
    m = _snap(0.97, 0.03)
    hv = HuntSignal(
        HuntType.NEAR_RESOLUTION_HV,
        edge_after_fees=0.03,
        confidence=0.97,
        details={"side": "yes", "stake_fraction": 0.75, "tier": "T1"},
    )
    sc = ScoredOpportunity(
        market=m,
        hunts=[hv],
        edge_size=0.03,
        confidence=0.97,
        liquidity_score=0.8,
        resolution_speed_score=0.9,
        strategy_performance_weight=0.5,
        score=0.9,
        tier=OpportunityTier.TIER_B,
        tier_sizing_multiplier=1.0,
    )
    intent = executor.build_execution_intent(
        sc,
        capital=100.0,
        outlet="kalshi",
        min_edge_effective=0.001,
    )
    assert intent is not None
    assert HuntType.NEAR_RESOLUTION_HV in intent.hunt_types
    assert intent.notional_usd >= 1.0
    assert intent.notional_usd <= 80.0


def test_polymarket_near_resolution_uses_90_floor():
    from trading_ai.shark.crypto_polymarket_hunts import hunt_near_resolution

    m = MarketSnapshot(
        market_id="p1",
        outlet="polymarket",
        yes_price=0.91,
        no_price=0.09,
        volume_24h=8000.0,
        time_to_resolution_seconds=900.0,
        resolution_criteria="x",
        last_price_update_timestamp=0.0,
        end_date_seconds=__import__("time").time() + 1200.0,
    )
    sig = hunt_near_resolution(m)
    assert sig is not None
    assert sig.hunt_type == HuntType.NEAR_RESOLUTION
    assert float(sig.details.get("stake_fraction", 0)) == pytest.approx(0.30, abs=0.01)
