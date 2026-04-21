"""Reality lock layer — fill truth and execution gates."""

from trading_ai.nte.reality_lock.fill_lock import FillMismatchAbort, aggregate_fills_to_stats, normalize_fill
from trading_ai.nte.reality_lock.fee_lock import check_fee_dominance_pre_trade
from trading_ai.nte.reality_lock.market_reality import check_market_reality_pre_trade


def test_normalize_fill_quote_sized_mistaken_for_base() -> None:
    # size matches filled_value in USD — must derive tiny base
    b, q, p = normalize_fill(
        {"price": "100000", "size": "9.87984212", "filled_value": "9.87984212"}
    )
    assert abs(b - 9.87984212 / 100000.0) < 1e-12
    assert abs(q - 9.87984212) < 1e-9
    assert p == 100000.0


def test_normalize_fill_base_sized() -> None:
    b, q, p = normalize_fill(
        {"price": "100000", "size": "0.0001", "filled_value": "10.0"}
    )
    assert abs(b - 0.0001) < 1e-12
    assert abs(q - 10.0) < 1e-9


def test_aggregate_fills() -> None:
    tb, av, fee = aggregate_fills_to_stats(
        [
            {"price": "100", "size": "1", "filled_value": "100", "commission": "0.01"},
        ]
    )
    assert abs(tb - 1.0) < 1e-9
    assert abs(av - 100.0) < 1e-9
    assert abs(fee - 0.01) < 1e-9


def test_fill_mismatch_aborts() -> None:
    try:
        normalize_fill({"price": "100", "size": "2", "filled_value": "50"})
    except FillMismatchAbort:
        return
    raise AssertionError("expected FillMismatchAbort")


def test_fee_dominance_blocks_bad_edge() -> None:
    ok, _ = check_fee_dominance_pre_trade(
        notional_usd=100.0,
        net_edge_bps=-1.0,
        est_round_trip_cost_bps=20.0,
        spread_bps=10.0,
    )
    assert not ok


def test_market_reality_blocks_wide_spread() -> None:
    ok, _ = check_market_reality_pre_trade(
        bid=100.0,
        ask=110.0,
        quote_volume_24h=1e9,
        net_edge_bps=50.0,
        spread_bps_est=5000.0,
    )
    assert not ok
