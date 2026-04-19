"""ACCO — PnL truth, base/quote safety, edge promotion wiring, fail-safe."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.edge.models import EdgeRecord, EdgeStatus
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.edge.validation import apply_evaluation
from trading_ai.organism.realized_pnl import compute_realized_pnl
from trading_ai.organism.trade_truth import TradeTruthError, abort_if_mismatch, assert_no_oversell, validate_trade_truth
from trading_ai.organism.pipeline import OrganismClosedTradeHook


def test_compute_realized_pnl_spot() -> None:
    r = compute_realized_pnl(
        instrument_kind="spot",
        fees=2.0,
        quote_qty_buy=100.0,
        quote_qty_sell=105.0,
    )
    assert r.gross_pnl == 5.0
    assert r.net_pnl == 3.0
    assert r.pnl_sign == 1


def test_compute_realized_pnl_prediction() -> None:
    r = compute_realized_pnl(
        instrument_kind="prediction",
        fees=0.5,
        contracts=10.0,
        entry_price_per_contract=0.4,
        payout_per_contract=1.0,
    )
    assert r.gross_pnl == pytest.approx(6.0)
    assert r.net_pnl == pytest.approx(5.5)


def test_compute_realized_pnl_options() -> None:
    r = compute_realized_pnl(
        instrument_kind="options",
        fees=1.25,
        contracts=2.0,
        entry_premium_per_contract=3.0,
        exit_premium_per_contract=4.0,
        option_multiplier=100.0,
    )
    assert r.gross_pnl == 200.0
    assert r.net_pnl == pytest.approx(198.75)


def test_base_quote_mismatch_aborts() -> None:
    with pytest.raises(TradeTruthError):
        abort_if_mismatch(1.0, 100.0, 50.0, label="test")


def test_validate_trade_truth_spot_ok() -> None:
    ok, err = validate_trade_truth(
        {
            "instrument_kind": "spot",
            "base_qty": 1.0,
            "avg_entry_price": 100.0,
            "quote_qty_buy": 100.0,
            "avg_exit_price": 101.0,
            "quote_qty_sell": 101.0,
        }
    )
    assert ok and err is None


def test_no_oversell() -> None:
    assert_no_oversell(position_base_before=1.0, sell_base_qty=1.0)
    with pytest.raises(TradeTruthError):
        assert_no_oversell(position_base_before=0.5, sell_base_qty=1.0)


def test_edge_promotion_requires_sample(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True)
    reg = EdgeRegistry()
    e = EdgeRecord(
        edge_id="edge_testpromo",
        avenue="kalshi",
        edge_type="probability",
        hypothesis_text="test",
        required_conditions={},
        status=EdgeStatus.TESTING.value,
    )
    reg.upsert(e)

    events = []
    for i in range(40):
        events.append(
            {
                "trade_id": f"t{i}",
                "edge_id": "edge_testpromo",
                "net_pnl": 1.0,
                "fees_paid": 0.1,
                "entry_slippage_bps": 1.0,
                "exit_slippage_bps": 1.0,
            }
        )
    monkeypatch.setenv("EDGE_MIN_SAMPLE_TRADES", "35")
    _, rep = apply_evaluation(reg, events, "edge_testpromo")
    assert rep.get("promote_to") == EdgeStatus.VALIDATED.value


def test_failsafe_pipeline_flag_in_hook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    db = tmp_path / "databank"
    db.mkdir(parents=True)
    (db / "trade_events.jsonl").write_text("", encoding="utf-8")

    out = OrganismClosedTradeHook.after_closed_trade(
        {"trade_id": "x", "net_pnl": 0.0},
        stages={"validated": True},
        pipeline_partial=True,
    )
    assert out["operating_mode"] in ("normal", "pressure", "opportunity")


def test_capital_allocation_weights_from_edge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trading_ai.edge.capital_allocation import allocation_weights_for_validated

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True)
    reg = EdgeRegistry()
    reg.upsert(
        EdgeRecord(
            edge_id="e1",
            avenue="kalshi",
            edge_type="probability",
            hypothesis_text="h",
            required_conditions={},
            status=EdgeStatus.VALIDATED.value,
        )
    )
    events = [{"edge_id": "e1", "net_pnl": 2.0, "fees_paid": 0.1} for _ in range(10)]
    aw = allocation_weights_for_validated(reg, events)
    assert "weights" in aw
    assert aw["weights"].get("e1", 0) >= 0.0
