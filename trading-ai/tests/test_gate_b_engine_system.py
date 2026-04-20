"""Gate B full pipeline + execution reality + unified portfolio (mocked)."""

from __future__ import annotations

from unittest.mock import patch

from trading_ai.shark.coinbase_spot.execution_reality import (
    AssetTier,
    midpoint_slippage_bps,
    simulate_execution_prices,
    theoretical_vs_actual_roundtrip_pnl_usd,
)
from trading_ai.shark.coinbase_spot.gate_b_engine import GateBMomentumEngine, demo_execution_summary
from trading_ai.shark.coinbase_spot.liquidity_gate import evaluate_liquidity_gate
from trading_ai.shark.coinbase_spot.breakout_filter import evaluate_breakout_entry
from trading_ai.shark.coinbase_spot.capital_allocation import gate_b_position_budgets_usd
from trading_ai.review.ceo_gate_reports import build_global_gate_report


def test_execution_reality_slippage_and_pnl() -> None:
    sim = simulate_execution_prices(
        intended_entry_price=100.0,
        intended_exit_price=110.0,
        tier=AssetTier.BTC_ETH,
    )
    assert sim["actual_entry_price"] >= sim["intended_entry_price"]
    assert sim["actual_exit_price"] <= sim["intended_exit_price"]
    pnl = theoretical_vs_actual_roundtrip_pnl_usd(
        base_qty=0.01,
        intended_entry=100.0,
        intended_exit=110.0,
        actual_entry=sim["actual_entry_price"],
        actual_exit=sim["actual_exit_price"],
        fees_usd=0.5,
    )
    assert pnl["theoretical_gross_pnl_usd"] >= pnl["actual_gross_pnl_usd"] - 1e-12
    assert midpoint_slippage_bps(AssetTier.LOW_CAP) > midpoint_slippage_bps(AssetTier.BTC_ETH)


def test_liquidity_gate_rejects_thin() -> None:
    out = evaluate_liquidity_gate(
        {"volume_24h_usd": 1000, "spread_bps": 200, "book_depth_usd": 100},
        min_volume_24h_usd=1e6,
        max_spread_bps=50,
        min_depth_usd=25_000,
    )
    assert out["passed"] is False
    assert out["liquidity_score"] < 0.5


def test_breakout_requires_volume_and_continuation() -> None:
    fail = evaluate_breakout_entry(
        {"move_pct": 0.06, "volume_surge_ratio": 1.0, "continuation_candles": 1},
        min_momentum_score=0.5,
    )
    assert fail["passed"] is False
    ok = evaluate_breakout_entry(
        {
            "move_pct": 0.06,
            "volume_surge_ratio": 1.8,
            "continuation_candles": 3,
            "velocity_score": 0.6,
            "candle_structure_score": 0.5,
        },
        min_momentum_score=0.4,
    )
    assert ok["passed"] is True


def test_gate_b_engine_end_to_end() -> None:
    eng = GateBMomentumEngine()
    rows = [
        {
            "product_id": "SOL-USD",
            "quote_ts": __import__("time").time(),
            "best_bid": 99.9,
            "best_ask": 100.1,
            "volume_24h_usd": 5e6,
            "spread_bps": 20,
            "book_depth_usd": 80_000,
            "move_pct": 0.055,
            "volume_surge_ratio": 1.6,
            "continuation_candles": 3,
            "velocity_score": 0.55,
            "candle_structure_score": 0.5,
            "exhaustion_risk": 0.2,
            "new_breakout_confirmed": True,
        }
    ]
    out = eng.evaluate_entry_candidates(rows, open_product_ids=[], regime_inputs={})
    assert out.get("gate_b_disabled") is not True
    assert isinstance(out.get("candidates"), list)
    assert isinstance(out.get("pre_rank_rejections"), list)
    assert out.get("gate_b_truth_version")

    st = __import__(
        "trading_ai.shark.coinbase_spot.gate_b_monitor",
        fromlist=["GateBMonitorState"],
    ).GateBMonitorState(
        product_id="SOL-USD",
        entry_price=100.0,
        peak_price=100.0,
        entry_ts=0.0,
        last_price=135.0,
    )
    # Gain 35% clears profit_zone_ceiling (>= max band); 11% alone sits in-zone without trail/max_hold.
    exits = eng.evaluate_exits([st], price_by_product={"SOL-USD": 135.0}, prev_price_by_product={"SOL-USD": 100.0}, now_ts=100.0)
    assert any(x.get("exit") for x in exits)


def test_gate_b_position_budgets() -> None:
    b = gate_b_position_budgets_usd(100_000.0, max_positions=4, regime_multiplier=1.0)
    assert abs(b["gate_b_pool_usd"] - 50_000.0) < 1.0


def test_demo_execution_summary() -> None:
    s = demo_execution_summary(
        product_id="BTC-USD",
        intended_entry=40_000.0,
        intended_exit=42_000.0,
        base_qty=0.01,
        fees_usd=2.0,
        liquidity_score=0.9,
    )
    assert "actual_net_pnl_usd" in s


def test_ceo_gate_reports() -> None:
    g = build_global_gate_report(gate_a={"win_rate": 0.5}, gate_b={"win_rate": 0.4}, capital_total_usd=1e5)
    assert g["gate_a"]["report"] == "gate_a"


def test_unified_portfolio_mock_accounts() -> None:
    from trading_ai.nte.unified_portfolio_coinbase import build_unified_coinbase_portfolio_usd

    accounts = [
        {"currency": "USD", "available_balance": {"value": "1000"}},
        {"currency": "BTC", "available_balance": {"value": "0.01"}},
    ]
    with patch(
        "trading_ai.nte.unified_portfolio_coinbase._ticker_mid_usd",
        return_value=50_000.0,
    ):
        u = build_unified_coinbase_portfolio_usd(accounts)
    assert u["total_usd_value"] >= 1000 + 0.01 * 50_000
