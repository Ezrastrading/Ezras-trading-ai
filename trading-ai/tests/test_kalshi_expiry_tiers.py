"""Kalshi expiry tier classification and batch filtering."""

from __future__ import annotations

import pytest

from trading_ai.shark.models import HuntSignal, HuntType, MarketSnapshot, OpportunityTier, ScoredOpportunity


def test_active_pool_tier_histogram_matches_snapshot_ttr():
    """Histogram uses _parse_close_time_seconds + classify (same path as MarketSnapshot)."""
    from trading_ai.shark.outlets.kalshi import _count_kalshi_rows_by_hv_expiry_tier

    now = 1_700_000_000.0
    rows = [
        {"close_time": now + 7 * 60},
        {"close_time": now + 20 * 60},
        {"close_time": now + 45 * 60},
        {"close_time": now + 90 * 60},
    ]
    c = _count_kalshi_rows_by_hv_expiry_tier(rows, now)
    assert c["A"] == 1
    assert c["B"] == 1
    assert c["C"] == 1
    assert c["outside"] == 1


def test_classify_default_windows():
    from trading_ai.shark.kalshi_expiry_tiers import classify_kalshi_expiry_tier

    assert classify_kalshi_expiry_tier(7 * 60) == "A"
    assert classify_kalshi_expiry_tier(15 * 60) == "B"
    assert classify_kalshi_expiry_tier(45 * 60) == "C"
    assert classify_kalshi_expiry_tier(60 * 60) == "C"
    assert classify_kalshi_expiry_tier(3 * 60) is None
    assert classify_kalshi_expiry_tier(90 * 60) is None


def test_hv_skips_tier_a_when_only_t3():
    from trading_ai.shark.kalshi_hunts import hunt_near_resolution_hv

    m = MarketSnapshot(
        market_id="x",
        outlet="kalshi",
        yes_price=0.91,
        no_price=0.09,
        volume_24h=5000.0,
        time_to_resolution_seconds=7 * 60,
        resolution_criteria="t",
        last_price_update_timestamp=0.0,
    )
    assert hunt_near_resolution_hv(m) is None


def test_hv_tier_a_t1_still_fires():
    from trading_ai.shark.kalshi_hunts import hunt_near_resolution_hv

    m = MarketSnapshot(
        market_id="x",
        outlet="kalshi",
        yes_price=0.97,
        no_price=0.03,
        volume_24h=5000.0,
        time_to_resolution_seconds=7 * 60,
        resolution_criteria="t",
        last_price_update_timestamp=0.0,
    )
    s = hunt_near_resolution_hv(m)
    assert s is not None
    assert s.details.get("kalshi_expiry_tier") == "A"


def test_doctrine_base_min_edge_by_tier():
    from trading_ai.shark.kalshi_expiry_tiers import kalshi_doctrine_base_min_edge

    assert kalshi_doctrine_base_min_edge(7 * 60) == pytest.approx(0.0080)
    assert kalshi_doctrine_base_min_edge(20 * 60) == pytest.approx(0.0150)
    assert kalshi_doctrine_base_min_edge(45 * 60) == pytest.approx(0.0200)


def test_resolution_speed_kalshi_uses_tiers():
    from trading_ai.shark.kalshi_expiry_tiers import resolution_speed_score_kalshi_tiers

    assert resolution_speed_score_kalshi_tiers(7 * 60) == pytest.approx(1.0)
    assert resolution_speed_score_kalshi_tiers(20 * 60) == pytest.approx(0.82)
    assert resolution_speed_score_kalshi_tiers(45 * 60) == pytest.approx(0.62)


def test_filter_drops_weak_c_when_ab_present():
    from trading_ai.shark.kalshi_expiry_tiers import filter_kalshi_hv_tier_c_when_ab_available

    def _m(ttr: float) -> MarketSnapshot:
        return MarketSnapshot(
            market_id="k",
            outlet="kalshi",
            yes_price=0.97,
            no_price=0.03,
            volume_24h=5000.0,
            time_to_resolution_seconds=ttr,
            resolution_criteria="t",
            last_price_update_timestamp=0.0,
        )

    hv = HuntSignal(HuntType.NEAR_RESOLUTION_HV, edge_after_fees=0.02, confidence=0.97, details={})
    ab = ScoredOpportunity(
        market=_m(8 * 60),
        hunts=[hv],
        edge_size=0.02,
        confidence=0.97,
        liquidity_score=0.5,
        resolution_speed_score=1.0,
        strategy_performance_weight=0.5,
        score=0.5,
        tier=OpportunityTier.TIER_B,
        tier_sizing_multiplier=1.0,
    )
    c_weak = ScoredOpportunity(
        market=_m(45 * 60),
        hunts=[HuntSignal(HuntType.NEAR_RESOLUTION_HV, edge_after_fees=0.02, confidence=0.97, details={})],
        edge_size=0.02,
        confidence=0.97,
        liquidity_score=0.5,
        resolution_speed_score=0.6,
        strategy_performance_weight=0.5,
        score=0.4,
        tier=OpportunityTier.TIER_B,
        tier_sizing_multiplier=1.0,
    )
    batch = [(ab, ab.market), (c_weak, c_weak.market)]
    out = filter_kalshi_hv_tier_c_when_ab_available(batch)
    assert len(out) == 1

    c_strong = ScoredOpportunity(
        market=_m(45 * 60),
        hunts=[HuntSignal(HuntType.NEAR_RESOLUTION_HV, edge_after_fees=0.10, confidence=0.97, details={})],
        edge_size=0.10,
        confidence=0.97,
        liquidity_score=0.5,
        resolution_speed_score=0.6,
        strategy_performance_weight=0.5,
        score=0.9,
        tier=OpportunityTier.TIER_B,
        tier_sizing_multiplier=1.0,
    )
    batch2 = [(ab, ab.market), (c_strong, c_strong.market)]
    out2 = filter_kalshi_hv_tier_c_when_ab_available(batch2)
    assert len(out2) == 2
