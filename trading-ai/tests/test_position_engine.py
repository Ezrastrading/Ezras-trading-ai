"""Tests for canonical spot position / PnL from base-sized fills."""

from trading_ai.core.position_engine import (
    Fill,
    FillSide,
    PositionState,
    compute_total_pnl,
    compute_unrealized_pnl,
    update_position_from_fill,
)


def test_buy_10_usd_notional_btc_correct_base_and_avg():
    """$10 notional at $50k/BTC → base from fill only (never inferred from quote alone)."""
    px = 50_000.0
    base = 10.0 / px
    fee = 0.02
    pos = PositionState(asset="BTC-USD")
    update_position_from_fill(
        pos,
        Fill(
            side=FillSide.BUY,
            base_size=base,
            quote_size=px * base,
            price=px,
            fee=fee,
        ),
    )
    assert abs(pos.base_size - base) < 1e-12
    assert abs(pos.quote_spent - (10.0 + fee)) < 1e-9
    assert abs(pos.avg_entry_price - pos.quote_spent / pos.base_size) < 1e-9
    assert pos.fees_paid == fee


def test_partial_sell_remaining_base():
    pos = PositionState(asset="BTC-USD")
    update_position_from_fill(
        pos,
        Fill(side="BUY", base_size=1.0, quote_size=30_000.0, price=30_000.0, fee=1.0),
    )
    update_position_from_fill(
        pos,
        Fill(side="SELL", base_size=0.4, quote_size=12_800.0, price=32_000.0, fee=0.5),
    )
    assert abs(pos.base_size - 0.6) < 1e-9
    assert pos.realized_pnl != 0.0
    assert pos.quote_spent > 0


def test_full_round_trip_pnl_matches_fees():
    """Buy then sell flat — lose roughly both legs' fees."""
    pos = PositionState(asset="BTC-USD")
    buy_px = 40_000.0
    base = 0.01
    buy_fee = 2.0
    update_position_from_fill(
        pos,
        Fill(
            side=FillSide.BUY,
            base_size=base,
            quote_size=buy_px * base,
            price=buy_px,
            fee=buy_fee,
        ),
    )
    sell_px = buy_px
    sell_fee = 1.5
    sell_quote = sell_px * base
    update_position_from_fill(
        pos,
        Fill(
            side=FillSide.SELL,
            base_size=base,
            quote_size=sell_quote,
            price=sell_px,
            fee=sell_fee,
        ),
    )
    assert pos.base_size == 0.0
    expected = -(buy_fee + sell_fee)
    assert abs(pos.realized_pnl - expected) < 1e-6
    assert abs(pos.fees_paid - (buy_fee + sell_fee)) < 1e-9


def test_unrealized_and_total():
    pos = PositionState(asset="ETH-USD")
    update_position_from_fill(
        pos,
        Fill(side="BUY", base_size=2.0, quote_size=6_000.0, price=3_000.0, fee=3.0),
    )
    u = compute_unrealized_pnl(pos, 3_100.0)
    assert abs(u - (3_100.0 - pos.avg_entry_price) * 2.0) < 1e-6
    t = compute_total_pnl(pos, 3_100.0)
    assert abs(t - (pos.realized_pnl + u)) < 1e-9
