"""Unit tests for trading_ai.intelligence discipline modules."""

from __future__ import annotations

import pytest

from trading_ai.intelligence.abstain_engine import MIN_CONFIDENCE, should_abstain
from trading_ai.intelligence.adaptive_sizing import compute_size_multiplier
from trading_ai.intelligence.capital_intelligence import shift_capital
from trading_ai.intelligence.edge_threshold import MIN_EDGE_USD, passes_edge_threshold
from trading_ai.intelligence.fee_engine import estimate_total_fees, is_trade_profitable
from trading_ai.intelligence.market_reality import evaluate_market_conditions


def test_estimate_total_fees_round_trip():
    assert estimate_total_fees(100.0, 0.001) == pytest.approx(0.2)


def test_fee_and_edge_threshold():
    assert is_trade_profitable(1.0, 0.5) is True
    assert is_trade_profitable(0.4, 0.5) is False
    ok, reason = passes_edge_threshold(1.0, 0.2)
    assert ok and reason is None
    ok2, reason2 = passes_edge_threshold(0.5, 0.2)
    assert not ok2 and reason2 == "edge_below_threshold"


def test_market_reality_pass():
    ob = {
        "bids": [[100.0, 10.0], [99.9, 5.0]],
        "asks": [[100.2, 10.0], [100.3, 5.0]],
    }
    r = evaluate_market_conditions(ob, trade_size=1.0)
    assert r["valid"] is True
    assert r["reason"] == "ok"
    assert r["spread_pct"] < 0.002


def test_market_reality_rejects_empty_book():
    r = evaluate_market_conditions(None, trade_size=10.0)
    assert r["valid"] is False
    assert r["reason"] == "no_orderbook"


def test_shift_capital_moves_weight():
    pnls = {"a": -10.0, "b": 50.0}
    w = {"a": 0.5, "b": 0.5}
    out = shift_capital(pnls, current_weights=w, shift_fraction=0.1, min_allocation=0.05)
    assert pytest.approx(sum(out.values()), abs=1e-6) == 1.0
    assert out["b"] > w["b"]


def test_abstain():
    assert should_abstain(MIN_CONFIDENCE, 0.05, True) is False
    assert should_abstain(MIN_CONFIDENCE - 0.01, 0.05, True) is True
    assert should_abstain(0.9, 0.05, False) is True


def test_adaptive_halt_on_many_losses():
    trades = [{"outcome": "loss", "pnl_usd": -1.0} for _ in range(12)]
    assert compute_size_multiplier(trades, 1000.0) == 0.0


def test_edge_threshold_constant():
    assert MIN_EDGE_USD == 0.30
