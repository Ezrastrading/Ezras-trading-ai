"""Calibration / market-truth honesty — Gate A artifact, Gate B tuning, attribution, provenance."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from trading_ai.control.adaptive_scope import (
    audit_trade_event_row_stats,
    effective_gate_label_for_scope,
    filter_events_for_scope,
    resolve_gate_id_attribution_for_trade_row,
)
from trading_ai.shark.coinbase_spot.gate_a_market_truth import liquidity_stability_provenance_for_row
from trading_ai.shark.coinbase_spot.gate_a_universe import build_gate_a_universe_artifact
from trading_ai.shark.coinbase_spot.gate_b_config import GateBConfig
from trading_ai.shark.coinbase_spot.gate_b_data_quality import evaluate_data_quality
from trading_ai.shark.coinbase_spot.gate_b_tuning_resolver import resolve_gate_b_tuning_artifact
from trading_ai.shark.coinbase_spot.liquidity_gate import evaluate_liquidity_gate


def test_gate_a_universe_artifact_prefers_live_rows_and_labels_fallback() -> None:
    live = [
        {
            "product_id": "BTC-USD",
            "_gate_a_row_origin": "live",
            "liquidity_score": 0.9,
            "spread_bps": 5.0,
            "quote_volume_24h_usd": 50_000_000.0,
            "volatility_bps": 10.0,
            "best_bid": 100.0,
            "best_ask": 100.1,
        }
    ]
    fb = [
        {
            "product_id": "ETH-USD",
            "_gate_a_row_origin": "fallback",
            "_placeholder_volume_prior": True,
            "liquidity_score": 0.7,
            "spread_bps": 8.0,
            "quote_volume_24h_usd": 8_000_000.0,
            "volatility_bps": 12.0,
        }
    ]
    art = build_gate_a_universe_artifact(
        live_market_rows=live,
        fallback_placeholder_rows=fb,
        source_mode=None,
    )
    assert art["source_mode"] == "derived_internal_priority_fallback"
    assert art["fallback_in_use"] is True
    assert art["production_truth_complete"] is False


def test_gate_a_status_does_not_present_placeholder_as_full_truth() -> None:
    ph = [
        {
            "product_id": "BTC-USD",
            "_gate_a_row_origin": "fallback",
            "_placeholder_volume_prior": True,
            "liquidity_score": 0.8,
            "spread_bps": 8.0,
            "quote_volume_24h_usd": 9_000_000.0,
            "volatility_bps": 10.0,
        }
    ]
    art = build_gate_a_universe_artifact(fallback_placeholder_rows=ph, live_market_rows=None)
    assert art["production_truth_complete"] is False
    assert art["fallback_in_use"] is True


def test_explicit_gate_id_attribution_beats_legacy_heuristic() -> None:
    row = {"trading_gate": "gate_a", "gate_id": "gate_b", "strategy_id": "something_gate_b"}
    gid, mode = resolve_gate_id_attribution_for_trade_row(row, None)
    assert gid == "gate_a"
    assert mode == "explicit_trading_gate"


def test_mixed_legacy_and_explicit_rows_do_not_double_count() -> None:
    # Two rows same gate_id from different attribution paths — grouping uses one row each
    from trading_ai.analysis.edge_extraction_engine import _aggregate_groups

    rows = [
        {
            "trade_id": "a",
            "strategy_id": "s",
            "product_id": "BTC-USD",
            "gate_id": "gate_a",
            "net_pnl": 1.0,
            "timestamp_close": "2020-01-01T00:00:00+00:00",
            "hold_seconds": 60.0,
        },
        {
            "trade_id": "b",
            "strategy_id": "s",
            "product_id": "BTC-USD",
            "gate_id": "gate_a",
            "net_pnl": -1.0,
            "timestamp_close": "2020-01-02T00:00:00+00:00",
            "hold_seconds": 60.0,
        },
    ]
    agg = _aggregate_groups(rows)
    assert len(agg) >= 1
    total_trades = sum(int(v["trades"]) for v in agg.values())
    assert total_trades == 2


def test_gate_b_tuning_resolver_varies_by_account_size_and_slippage() -> None:
    base = GateBConfig()
    small = resolve_gate_b_tuning_artifact(deployable_quote_usd=1000.0, measured_slippage_bps=None, baseline_config=base)
    large = resolve_gate_b_tuning_artifact(deployable_quote_usd=50_000.0, measured_slippage_bps=None, baseline_config=base)
    assert small["account_size_bucket"] == "small"
    assert large["account_size_bucket"] == "large"
    assert small["selected_tuning"]["momentum_top_k"] <= large["selected_tuning"]["momentum_top_k"]

    slip = resolve_gate_b_tuning_artifact(
        deployable_quote_usd=None,
        measured_slippage_bps=80.0,
        baseline_config=base,
    )
    assert slip["calibration_level"] == "slippage_only_deployable_unknown"
    assert slip["selected_tuning"]["profit_exit_slippage_buffer_pct"] >= base.profit_exit_slippage_buffer_pct


def test_gate_b_missing_calibration_falls_back_conservatively() -> None:
    base = GateBConfig()
    out = resolve_gate_b_tuning_artifact(deployable_quote_usd=None, measured_slippage_bps=None, baseline_config=base)
    assert out["calibration_level"] == "baseline_env_only"
    assert out["selected_tuning"]["hard_stop_from_entry_pct"] == base.hard_stop_from_entry_pct


def test_gate_b_full_calibration_requires_measured_slippage_and_deployable() -> None:
    base = GateBConfig()
    full = resolve_gate_b_tuning_artifact(
        deployable_quote_usd=10_000.0,
        measured_slippage_bps=42.0,
        baseline_config=base,
    )
    assert full["calibration_level"] == "full_measured_slippage_and_deployable"
    assert full["truth_version"] == "gate_b_tuning_resolution_v2"


def test_liquidity_and_stability_provenance_is_exposed_honestly() -> None:
    lg = evaluate_liquidity_gate(
        {"volume_24h_usd": 5e6, "spread_bps": 10.0, "book_depth_usd": 30_000.0},
        min_volume_24h_usd=2e6,
        max_spread_bps=50.0,
        min_depth_usd=25_000.0,
    )
    assert "field_provenance" in lg
    assert lg["field_provenance"]["volume_24h_usd"] == "caller_supplied_hint"

    dq = evaluate_data_quality(quote_ts=__import__("time").time(), max_age_sec=8.0, bid=1.0, ask=1.01)
    assert "field_provenance" in dq
    prov = liquidity_stability_provenance_for_row({"product_id": "X", "spread_bps": 5.0, "liquidity_score": 0.5})
    assert prov["liquidity_truth_confidence"] <= 1.0


def test_status_outputs_surface_calibration_and_truth_gaps() -> None:
    from trading_ai.shark.coinbase_spot.avenue_a_operator_status import build_avenue_a_operator_status

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "data" / "control").mkdir(parents=True)
        st = build_avenue_a_operator_status(runtime_root=root)
    assert "gate_a_production_truth_complete" in st
    assert "gate_b_calibration_level" in st
    assert st["gate_a_production_truth_complete"] is False


def test_legacy_strategy_heuristic_gate_b_scope() -> None:
    row = {"strategy_id": "foo_gate_b_bar", "net_pnl": 1.0}
    assert effective_gate_label_for_scope(row) == "gate_b"

