"""Post-trade reality hardening: execution truth, fill quality, snapshot, alerts (isolated roots)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.organism.pipeline import OrganismClosedTradeHook
from trading_ai.reality.execution_truth import compute_execution_truth, execution_truth_path
from trading_ai.reality.paths import reality_data_dir


def _minimal_trade(
    tid: str,
    *,
    net: float = 0.42,
    gross: float = 0.45,
    fees: float = 0.03,
    fill_s: float = 2.0,
    partials: int = 0,
    intended_e: float = 100.0,
    actual_e: float = 100.0,
    intended_x: float = 101.0,
    actual_x: float = 101.0,
) -> dict:
    return {
        "trade_id": tid,
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "asset": "BTC-USD",
        "strategy_id": "s1",
        "route_chosen": "A",
        "regime": "calm",
        "timestamp_open": "2026-04-19T10:00:00+00:00",
        "timestamp_close": "2026-04-19T10:05:00+00:00",
        "gross_pnl": gross,
        "fees_paid": fees,
        "net_pnl": net,
        "intended_entry_price": intended_e,
        "actual_entry_price": actual_e,
        "intended_exit_price": intended_x,
        "actual_exit_price": actual_x,
        "base_qty": 1.0,
        "fill_seconds": fill_s,
        "partial_fill_count": partials,
        "stale_cancelled": False,
        "entry_slippage_bps": 0.0,
        "exit_slippage_bps": 0.0,
    }


def test_execution_truth_fees_plus_slippage_ratio() -> None:
    """High fees + slippage vs small gross flags killing edge."""
    ex = compute_execution_truth(
        expected_entry_price=100.0,
        actual_entry_price=100.5,
        expected_exit_price=100.2,
        actual_exit_price=100.2,
        base_size=1.0,
        fees_paid=0.15,
    )
    assert ex.slippage_usd > 0.0
    assert ex.execution_drag_ratio == pytest.approx((ex.fees_paid + ex.slippage_usd) / abs(ex.gross_pnl))
    if ex.execution_drag_ratio > 0.5:
        assert ex.flag == "EXECUTION_KILLING_EDGE"


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ez"
    root.mkdir()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(root / "databank"))
    os.makedirs(root / "databank", exist_ok=True)
    return root


def test_three_trades_pipeline_files(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.nte.databank.local_trade_store import append_jsonl_atomic, global_trade_events_path
    from trading_ai.monitoring.fill_quality import fill_quality_log_path
    from trading_ai.control.reality_snapshot import reality_snapshot_path
    from trading_ai.control.paths import alerts_txt_path

    monkeypatch.setenv("EZRAS_OVERTRADING_GUARD", "0")

    path = global_trade_events_path()
    for i, extra in enumerate(
        [
            {"net": 0.40, "gross": 0.42},
            {"net": 0.38, "gross": 0.40},
            {"net": -0.12, "gross": -0.10},
        ]
    ):
        raw = _minimal_trade(f"sim-{i}", **extra)
        append_jsonl_atomic(path, raw, trade_id=raw["trade_id"])

        out = OrganismClosedTradeHook.after_closed_trade(raw, stages={"validated": True})
        assert "post_trade_reality" in out

    ex_path = execution_truth_path()
    assert ex_path.is_file()
    lines = [json.loads(x) for x in ex_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 3
    assert all("gross_pnl" in x for x in lines)

    fq = fill_quality_log_path()
    assert fq.is_file()
    fq_lines = fq.read_text(encoding="utf-8").strip().splitlines()
    assert len(fq_lines) == 3

    snap = reality_snapshot_path()
    assert snap.is_file()
    txt = snap.read_text(encoding="utf-8")
    assert "LAST TRADE" in txt
    assert "EXECUTION HEALTH" in txt

    assert reality_data_dir().resolve() == (isolated / "data" / "reality").resolve()


def test_poor_fill_quality_alert(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.monitoring.fill_quality import append_fill_quality_log, evaluate_fill_quality
    from trading_ai.control.paths import alerts_txt_path

    m = _minimal_trade("slow-fill", fill_s=15.0, partials=0)
    ev = evaluate_fill_quality(m)
    assert ev["poor_fill_quality"] is True
    append_fill_quality_log(m, evaluation=ev)
    alert = alerts_txt_path()
    assert alert.is_file()
    assert "Poor fill quality" in alert.read_text(encoding="utf-8")


def test_execution_degrading_alert(isolated: Path) -> None:
    from trading_ai.reality.execution_truth import append_execution_truth_record

    ex = compute_execution_truth(
        expected_entry_price=50.0,
        actual_entry_price=50.0,
        expected_exit_price=51.0,
        actual_exit_price=51.0,
        base_size=1.0,
        fees_paid=40.0,
    )
    assert ex.flag == "EXECUTION_KILLING_EDGE"
    append_execution_truth_record(ex)
    from trading_ai.control.paths import alerts_txt_path

    assert "Execution degrading edge" in alerts_txt_path().read_text(encoding="utf-8")
