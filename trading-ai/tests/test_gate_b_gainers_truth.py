"""Gate B gainers selection — honest spread fields (no fake 9999 bps as measurement)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection import (
    _analyze_ticker_for_spread,
    _normalize_coinbase_ticker_bid_ask,
    run_gate_b_gainers_selection,
)
from trading_ai.shark.coinbase_spot.liquidity_gate import evaluate_liquidity_gate


def test_normalize_advanced_trade_best_bid_ask() -> None:
    j = _normalize_coinbase_ticker_bid_ask({"best_bid": "100", "best_ask": "100.2", "price": "100.1"})
    assert float(j["bid"]) == 100.0
    assert float(j["ask"]) == 100.2


def test_analyze_measured_spread_from_best_bid_ask() -> None:
    j = _normalize_coinbase_ticker_bid_ask(
        {"best_bid": "100", "best_ask": "100.1", "price": "100.05", "time": str(time.time())}
    )
    out = _analyze_ticker_for_spread(j, max_quote_age_sec=999999.0)
    assert out["spread_measurement_status"] == "measured"
    assert out["measured_spread_bps"] is not None
    assert out["measured_spread_bps"] == pytest.approx((0.1 / 100.05) * 10000.0, rel=1e-6)
    assert out["selection_rejection_category"] == "none"


def test_analyze_missing_bid_ask_not_measured_spread() -> None:
    out = _analyze_ticker_for_spread({"price": "1"}, max_quote_age_sec=999999.0)
    assert out["spread_measurement_status"] == "unavailable"
    assert out["measured_spread_bps"] is None
    assert out["selection_rejection_category"] == "missing_or_stale_quote"


def test_liquidity_gate_missing_spread_fail_closed_not_9999_bps() -> None:
    lg = evaluate_liquidity_gate(
        {"volume_24h_usd": 5e6, "book_depth_usd": 30_000.0},
        min_volume_24h_usd=2e6,
        max_spread_bps=50.0,
        min_depth_usd=25_000.0,
    )
    assert lg["passed"] is False
    assert lg["spread_measurement_status"] == "unavailable"
    assert lg["measured_spread_bps"] is None
    assert "spread_not_measured_fail_closed" in lg["reject_reasons"]
    assert "spread_above_max" not in lg["reject_reasons"]


def test_run_gate_b_gainers_selection_mocked_ticker(tmp_path: Path) -> None:
    (tmp_path / "data" / "control").mkdir(parents=True)

    def fake_req(path: str) -> dict:
        assert "/ticker" in path
        return {"best_bid": "50", "best_ask": "50.05", "price": "50.025", "time": str(time.time())}

    with patch(
        "trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection.ordered_validation_candidates",
        return_value=["BTC-USD"],
    ), patch(
        "trading_ai.shark.outlets.coinbase._brokerage_public_request",
        side_effect=fake_req,
    ):
        out = run_gate_b_gainers_selection(runtime_root=tmp_path, client=None)
    assert out["truth_version"] == "gate_b_selection_snapshot_v3"
    assert out.get("gate_b_truth_version")
    assert out["selected_symbols"] == ["BTC-USD"]
    row = out["ranked_gainer_candidates"][0]
    assert row["spread_measurement_status"] == "measured"
    assert row["measured_spread_bps"] is not None


def test_gate_b_gainers_capital_split_fail_closed_empty_selection(tmp_path: Path) -> None:
    (tmp_path / "data" / "control").mkdir(parents=True)
    fake_split = {"ok": False, "failure_reason": "deployable_usd_not_computable_or_fractions_invalid"}
    with patch(
        "trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection.compute_coinbase_gate_capital_split",
        return_value=fake_split,
    ), patch(
        "trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection.ordered_validation_candidates",
        return_value=[],
    ):
        out = run_gate_b_gainers_selection(
            runtime_root=tmp_path,
            client=None,
            deployable_quote_usd=float("nan"),
        )
    assert out["selected_symbols"] == []
    assert out["selection_summary"]["gate_b_selection_state"] == "empty_capital_gate"
    assert out["capital_budget_allocated_usd"] is None
