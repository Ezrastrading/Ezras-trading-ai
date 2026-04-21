"""Tests for reality validation + trade logging (isolated temp roots)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.reality.discipline_engine import DisciplineEngine
from trading_ai.reality.edge_truth import EdgeTruthEngine
from trading_ai.reality.execution_truth import compute_execution_truth
from trading_ai.reality.orchestrator import record_closed_trade
from trading_ai.reality.sample_validation import validate_sample
from trading_ai.reality.trade_logger import milestone_verdict
from trading_ai.reality.verdict import build_reality_verdict


@pytest.fixture()
def isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "rt"
    root.mkdir()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    return root


def test_execution_truth_killing_flag(isolated_roots: Path) -> None:
    ex = compute_execution_truth(
        expected_entry_price=100.0,
        actual_entry_price=100.0,
        expected_exit_price=101.0,
        actual_exit_price=101.0,
        base_size=1.0,
        fees_paid=60.0,
    )
    assert ex.gross_pnl == pytest.approx(1.0)
    assert ex.net_pnl == pytest.approx(-59.0)
    assert ex.execution_drag_ratio > 0.5
    assert ex.slippage_usd == pytest.approx(0.0)
    assert ex.flag == "EXECUTION_KILLING_EDGE"


def test_edge_truth_windows(isolated_roots: Path) -> None:
    eng = EdgeTruthEngine(data_dir=isolated_roots / "reality")
    for _ in range(25):
        eng.record_trade("e1", gross_pnl=1.0, net_pnl=0.5)
    s = eng.summary_for_edge("e1")
    assert s["trade_count"] == 25
    w20 = s["windows"]["20"]
    assert w20["net_expectancy"] > 0
    assert w20["edge_status"] == "REAL_EDGE"


def test_discipline_break_and_cooldown(isolated_roots: Path) -> None:
    de = DisciplineEngine(data_dir=isolated_roots / "disc")
    r1 = de.evaluate(["trade_taken_when_blocked"])
    assert r1.discipline_score == 75
    assert r1.mark == "DISCIPLINE_BREAK"
    for _ in range(2):
        de.evaluate(["trade_outside_regime", "oversize_trade"])
    r = de.evaluate([])
    assert r.cooldown_triggered is True


def test_sample_validation(isolated_roots: Path) -> None:
    nets = [1.0] * 60
    v = validate_sample(nets)
    assert v.confidence_level == "HIGH"
    assert v.mark == "VALIDATED_EDGE"


def test_record_closed_trade_pipeline(isolated_roots: Path) -> None:
    ee = EdgeTruthEngine(data_dir=isolated_roots / "reality")
    de = DisciplineEngine(data_dir=isolated_roots / "disc")
    out = record_closed_trade(
        timestamp="2026-04-19T12:00:00+00:00",
        venue="kalshi",
        edge_id="mr_micro",
        product="KX-TEST",
        expected_entry_price=50.0,
        actual_entry_price=50.1,
        expected_exit_price=51.0,
        actual_exit_price=50.9,
        base_size=10.0,
        fees_paid=2.0,
        regime="trend",
        latency_ms=120.0,
        expected_edge_bps=50.0,
        violations=[],
        edge_engine=ee,
        discipline_engine=de,
    )
    assert "verdict" in out
    raw = isolated_roots / "data" / "trade_logs" / "trades_raw.jsonl"
    assert raw.is_file()
    line = raw.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["venue"] == "kalshi"
    assert "net_pnl" in rec


def test_milestone_verdict_gates() -> None:
    assert milestone_verdict(trade_count=15, cumulative_net_pnl=10.0, net_expectancy=1.0, drawdown_acceptable=True) == "INSUFFICIENT_SAMPLE"
    assert milestone_verdict(trade_count=20, cumulative_net_pnl=-1.0, net_expectancy=1.0, drawdown_acceptable=True) == "NO_EDGE"
    assert milestone_verdict(trade_count=50, cumulative_net_pnl=10.0, net_expectancy=-0.1, drawdown_acceptable=True) == "FALSE_EDGE"
    assert milestone_verdict(trade_count=100, cumulative_net_pnl=10.0, net_expectancy=0.5, drawdown_acceptable=True) == "REAL_EDGE_CONFIRMED"
    assert milestone_verdict(trade_count=100, cumulative_net_pnl=10.0, net_expectancy=0.5, drawdown_acceptable=False) == "REAL_EDGE_PENDING_DRAWDOWN"


def test_verdict_real_noise(isolated_roots: Path) -> None:
    eng = EdgeTruthEngine(data_dir=isolated_roots / "reality")
    for _ in range(25):
        eng.record_trade("e2", gross_pnl=2.0, net_pnl=1.5)
    summary = eng.summary_for_edge("e2")
    nets = eng.net_pnls("e2")
    sr = validate_sample(nets).to_dict()
    v = build_reality_verdict(
        edge_id="e2",
        venue="x",
        edge_summary=summary,
        net_pnls_for_edge=nets,
        execution_flag="OK",
        discipline_score=95,
        sample_result=sr,
    )
    assert v["verdict"] == "REAL"
