import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trading_ai.shark.mission import evaluate_trade_against_mission, mission_cap_fraction_set, mission_cap_fraction_reset


def test_probability_below_63_is_blocked() -> None:
    r = evaluate_trade_against_mission("kalshi", "KXBTC", 1.0, 0.62, 200.0)
    assert r["approved"] is False
    assert r["probability_tier"] == 0


def test_tier1_allows_but_caps_size() -> None:
    # Tier 1 cap is 5% of balance; $200 -> $10 max
    ok = evaluate_trade_against_mission("kalshi", "KXBTC", 5.0, 0.70, 200.0)
    big = evaluate_trade_against_mission("kalshi", "KXBTC", 12.0, 0.70, 200.0)
    assert ok["approved"] is True
    assert big["approved"] is False
    assert ok["probability_tier"] == 1


def test_tier2_is_less_restrictive_than_tier1() -> None:
    # Same $12 sizing is blocked at tier 1 but allowed at tier 2 under tier caps.
    r = evaluate_trade_against_mission("kalshi", "KXBTC", 12.0, 0.80, 200.0)
    assert r["approved"] is True
    assert r["probability_tier"] == 2


def test_tier3_allows_largest_sizing_within_hard_caps() -> None:
    r = evaluate_trade_against_mission("kalshi", "KXBTC", 30.0, 0.92, 200.0)
    assert r["approved"] is True
    assert r["probability_tier"] == 3


def test_live_micro_cap_override_allows_25_percent_not_20() -> None:
    tok = mission_cap_fraction_set(0.25)
    try:
        r = evaluate_trade_against_mission("coinbase", "BTC-USD", 9.03, 0.90, 36.10)
        assert r["approved"] is True
    finally:
        mission_cap_fraction_reset(tok)

