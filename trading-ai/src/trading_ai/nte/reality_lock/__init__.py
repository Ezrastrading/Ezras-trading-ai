"""Reality lock layer — execution safety, fill truth, position truth, and venue isolation."""

from trading_ai.nte.reality_lock.capital_velocity import (
    capital_velocity_allows_trade,
    record_trade_executed,
)
from trading_ai.nte.reality_lock.fee_lock import check_fee_dominance_pre_trade, fee_dominance_from_dec
from trading_ai.nte.reality_lock.fill_lock import FillMismatchAbort, aggregate_fills_to_stats, normalize_fill
from trading_ai.nte.reality_lock.halt import raise_and_halt, reality_halt
from trading_ai.nte.reality_lock.idempotency import RecentOrderIdTracker, global_order_tracker
from trading_ai.nte.reality_lock.market_reality import (
    check_market_reality_pre_trade,
    check_order_timestamp_fresh,
    spread_bps,
    volume_proxy_1m_usd,
)
from trading_ai.nte.reality_lock.order_confirm import wait_for_fill
from trading_ai.nte.reality_lock.position_lock import (
    assert_no_oversell_strict,
    assert_post_fill_desync,
    base_currency_for_product,
    reconcile_coinbase_spot_base,
    reconcile_position,
)
from trading_ai.nte.reality_lock.venue_state import VenueState, get_venue_state, set_venue_shutdown

__all__ = [
    "FillMismatchAbort",
    "RecentOrderIdTracker",
    "VenueState",
    "aggregate_fills_to_stats",
    "assert_no_oversell_strict",
    "assert_post_fill_desync",
    "base_currency_for_product",
    "capital_velocity_allows_trade",
    "check_fee_dominance_pre_trade",
    "check_market_reality_pre_trade",
    "check_order_timestamp_fresh",
    "fee_dominance_from_dec",
    "get_venue_state",
    "global_order_tracker",
    "normalize_fill",
    "raise_and_halt",
    "reality_halt",
    "reconcile_coinbase_spot_base",
    "reconcile_position",
    "record_trade_executed",
    "set_venue_shutdown",
    "spread_bps",
    "volume_proxy_1m_usd",
    "wait_for_fill",
]
