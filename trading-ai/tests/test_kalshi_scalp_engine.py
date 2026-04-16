"""Unit tests for Kalshi scalp engine (paper / mocked REST)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from trading_ai.shark.kalshi_scalp_config import KalshiScalpConfig
from trading_ai.shark.kalshi_scalp_market_filter import (
    LiquiditySnapshot,
    MarketFamily,
    evaluate_scalp_filter,
    parse_orderbook_yes_no_best_bid_ask_cents,
)
from trading_ai.shark.kalshi_scalp_position_manager import KalshiScalpPositionManager, ScalpTrade
from trading_ai.shark.kalshi_scalp_scanner import ScalpSetup
def test_parse_orderbook_bid_ask_cents():
    ob = {"orderbook": {"yes": [[48, 100], [52, 10]], "no": [[49, 20], [53, 5]]}}
    yb, ya, nb, na = parse_orderbook_yes_no_best_bid_ask_cents(ob)
    assert yb == 52
    assert ya == 48
    assert nb == 53
    assert na == 49


def test_classify_family_sp_btc_eth():
    cfg = KalshiScalpConfig()
    from trading_ai.shark.kalshi_scalp_market_filter import classify_market_family

    assert classify_market_family("KXINX-24APR15-T5000", "", cfg) == MarketFamily.SP
    assert classify_market_family("KXBTC-24APR15", "", cfg) == MarketFamily.BTC
    assert classify_market_family("KXETH-24APR15", "", cfg) == MarketFamily.ETH


def test_filter_accepts_tight_book():
    cfg = KalshiScalpConfig(min_volume_fp=0.0, max_spread_prob=0.10, min_top_of_book_contracts=1.0)
    m = {
        "ticker": "KXBTC-TEST",
        "series_ticker": "KXBTC",
        "status": "open",
        "volume_fp": 100.0,
        "close_time": "2099-01-01T23:59:59+00:00",
    }
    ob = {
        "orderbook": {
            "yes": [[50, 50], [52, 50]],
            "no": [[48, 50], [50, 50]],
        }
    }
    fr = evaluate_scalp_filter(m, ob, cfg=cfg, now=time.time())
    assert fr.ok


def test_duplicate_exit_not_submitted_twice():
    cfg = KalshiScalpConfig(paper_mode=True, execution_enabled=False)
    pm = KalshiScalpPositionManager(cfg, client=MagicMock(), metrics=MagicMock())
    trade = ScalpTrade(
        trade_id="t1",
        state="OPEN",
        market_ticker="KX-X",
        family="BTC",
        side="yes",
        entry_price_prob=0.50,
        size_contracts=10.0,
        profit_target_usd=0.04,
        stop_loss_usd=-0.04,
        soft_timeout_sec=60.0,
        hard_timeout_sec=120.0,
        entry_time=time.time(),
        entry_bid_depth=100.0,
    )
    pm.client.place_order = MagicMock()
    pm._orderbook = MagicMock(
        return_value={
            "orderbook": {
                "yes": [[50, 100], [51, 100]],
                "no": [[49, 100], [50, 100]],
            }
        }
    )
    trade.exit_submitted_at = time.time()
    trade.exit_reason = "test"
    out = pm.execute_exit(trade, "duplicate_test")
    pm.client.place_order.assert_not_called()
    assert out is trade


def test_engine_run_step_paper(monkeypatch):
    from trading_ai.shark.kalshi_scalp_engine import KalshiScalpEngine

    cfg = KalshiScalpConfig(
        paper_mode=True,
        execution_enabled=False,
        scanner_interval_seconds=0.0,
        position_check_interval_seconds=0.0,
        session_restrict_et=False,
        max_trade_attempts_per_hour=100,
        max_completed_trades_per_hour=1,
    )
    liq = LiquiditySnapshot(
        yes_bid_cents=49,
        yes_ask_cents=50,
        no_bid_cents=49,
        no_ask_cents=50,
        yes_bid_sz=100.0,
        yes_ask_sz=100.0,
        no_bid_sz=100.0,
        no_ask_sz=100.0,
    )
    setup = ScalpSetup(
        family=MarketFamily.BTC,
        market_ticker="KXBTC-DEMO",
        side="yes",
        score=1.0,
        ask_cents=50,
        bid_cents=49,
        spread_cents=2.0,
        contracts=2,
        market_row={"ticker": "KXBTC-DEMO", "series_ticker": "KXBTC", "status": "open"},
        orderbook={"orderbook": {"yes": [[49, 100], [50, 100]], "no": [[50, 100], [51, 100]]}},
        liquidity=liq,
    )

    scanner = MagicMock()
    scanner.scan_best_setup = MagicMock(return_value=(setup, {"candidates_found": 1, "raw_markets": 1}))
    eng = KalshiScalpEngine(cfg=cfg, scanner=scanner)
    eng.pm._orderbook = MagicMock(
        return_value={
            "orderbook": {
                "yes": [[50, 1000], [55, 100]],
                "no": [[45, 1000], [50, 100]],
            }
        }
    )
    eng._market_row = MagicMock(
        return_value={
            "ticker": "KXBTC-DEMO",
            "series_ticker": "KXBTC",
            "status": "open",
            "volume_fp": 500.0,
            "close_time": "2099-01-01T23:59:59+00:00",
        }
    )

    eng.run_step()
    assert eng.active_trade is not None
    assert eng.active_trade.state == "OPEN"

    eng.pm._orderbook = MagicMock(
        return_value={
            "orderbook": {
                "yes": [[60, 1000], [61, 100]],
                "no": [[39, 1000], [40, 100]],
            }
        }
    )
    eng.run_step()
    assert eng.active_trade is None
    assert eng.metrics.exits_by_target >= 1
