"""Spot inventory reconciliation (delta mode) and Gate B pure-logic tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading_ai.shark.coinbase_spot.capital_allocation import compute_gate_allocation_split
from trading_ai.shark.coinbase_spot.gate_b_config import GateBConfig
from trading_ai.shark.coinbase_spot.gate_b_monitor import GateBMonitorState, gate_b_monitor_tick
from trading_ai.shark.coinbase_spot.gate_b_scanner import rank_gate_b_candidates
from trading_ai.knowledge.spot_inventory_ontology import spot_equity_usd, unrealized_pnl_usd


def test_reconciliation_delta_passes_with_preexisting_dust() -> None:
    from trading_ai.deployment import reconciliation_proof as rp

    fake_accts = [
        {"currency": "BTC", "available_balance": {"value": "0.00013095"}},
    ]
    mock_cc = MagicMock()
    mock_cc.has_credentials.return_value = True
    mock_cc.list_all_accounts.return_value = fake_accts

    ctx = {
        "product_id": "BTC-USD",
        "baseline_exchange_base_qty": 0.00013095,
        "baseline_internal_base_qty": 0.0,
        "reconciliation_mode": "inventory_delta",
    }
    with patch.object(rp, "load_positions", return_value={"open_positions": []}):
        with patch("trading_ai.shark.outlets.coinbase.CoinbaseClient", return_value=mock_cc):
            out = rp.prove_reconciliation_after_trade(ctx, append_log=False, btc_tolerance=1e-4)
    assert out.get("reconciliation_ok") is True


def test_reconciliation_delta_fails_on_divergence() -> None:
    from trading_ai.deployment import reconciliation_proof as rp

    fake_accts = [
        {"currency": "BTC", "available_balance": {"value": "0.51"}},
    ]
    mock_cc = MagicMock()
    mock_cc.has_credentials.return_value = True
    mock_cc.list_all_accounts.return_value = fake_accts

    ctx = {
        "product_id": "BTC-USD",
        "baseline_exchange_base_qty": 0.0,
        "baseline_internal_base_qty": 0.0,
        "reconciliation_mode": "inventory_delta",
    }
    with patch.object(rp, "load_positions", return_value={"open_positions": []}):
        with patch("trading_ai.shark.outlets.coinbase.CoinbaseClient", return_value=mock_cc):
            out = rp.prove_reconciliation_after_trade(ctx, append_log=False, btc_tolerance=1e-4)
    assert out.get("reconciliation_ok") is False


def test_strict_absolute_still_fails_preexisting_without_baseline() -> None:
    from trading_ai.deployment import reconciliation_proof as rp

    fake_accts = [
        {"currency": "BTC", "available_balance": {"value": "0.00013095"}},
    ]
    mock_cc = MagicMock()
    mock_cc.has_credentials.return_value = True
    mock_cc.list_all_accounts.return_value = fake_accts

    with patch.object(rp, "load_positions", return_value={"open_positions": []}):
        with patch("trading_ai.shark.outlets.coinbase.CoinbaseClient", return_value=mock_cc):
            out = rp.prove_reconciliation_after_trade({"product_id": "BTC-USD"}, append_log=False, btc_tolerance=1e-4)
    assert out.get("reconciliation_ok") is False


def test_internal_base_filters_by_product() -> None:
    from trading_ai.nte.spot_inventory_snapshot import internal_open_base_qty_for_asset

    positions = {
        "open_positions": [
            {"outlet": "coinbase", "product_id": "ETH-USD", "base_qty": 0.02},
            {"outlet": "coinbase", "product_id": "BTC-USD", "base_qty": 0.001},
        ]
    }
    assert abs(internal_open_base_qty_for_asset(positions, "BTC") - 0.001) < 1e-12


def test_gate_allocation_split_defaults() -> None:
    s = compute_gate_allocation_split()
    assert abs(s.gate_a + s.gate_b - 1.0) < 1e-9
    assert abs(s.gate_a_majors + s.gate_a_other - 1.0) < 1e-9


def test_gate_b_ranker_and_monitor() -> None:
    acc, rej = rank_gate_b_candidates(
        [
            {"product_id": "AAA-USD", "momentum_score": 0.8, "liquidity_score": 0.9, "exhaustion_risk": 0.1},
            {"product_id": "ILL-USD", "momentum_score": 0.1, "liquidity_score": 0.1, "exhaustion_risk": 0.2},
        ]
    )
    assert acc and acc[0].product_id == "AAA-USD"
    assert rej

    st = GateBMonitorState(
        product_id="X-USD",
        entry_price=100.0,
        peak_price=100.0,
        entry_ts=0.0,
        last_price=112.0,
    )
    r = gate_b_monitor_tick(st, now_ts=10.0, profit_target_pct=0.10, profit_zone_max_pct=0.11)
    assert r["exit"] is True
    assert r["exit_reason"] == "profit_zone_ceiling"

    st2 = GateBMonitorState(
        product_id="Y-USD",
        entry_price=100.0,
        peak_price=110.0,
        entry_ts=0.0,
        last_price=106.0,
    )
    r2 = gate_b_monitor_tick(st2, now_ts=20.0, trailing_stop_from_peak_pct=0.03)
    assert r2["exit"] is True
    assert r2["exit_reason"] == "trailing_stop_from_peak"


def test_spot_ontology_equity() -> None:
    eq = spot_equity_usd(quote_usd=100.0, quote_usdc=0.0, base_qty=0.01, mark_usd_per_base=50_000.0)
    assert eq == 100.0 + 500.0
    u = unrealized_pnl_usd(mark=51000.0, avg_entry=50000.0, base_qty=0.01)
    assert abs(u - 10.0) < 1e-9


def test_ceo_session_engine_smoke() -> None:
    from trading_ai.review.ceo_session_engine import run_ceo_session_bundle

    b = run_ceo_session_bundle(metrics_gate_a={"pnl": 1.0}, metrics_gate_b={"wins": 2})
    assert "CEO_A_SESSION" in b and "CEO_GLOBAL_SESSION" in b
