"""First-20 diagnostic monitor — scenarios from spec (no fake passes)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_ai.first_20.constants import P_OPERATOR_ACK, P_PAUSE_REASON, P_TRUTH, PhaseStatus
from trading_ai.first_20.engine import activate_diagnostic_phase, process_closed_trade
from trading_ai.first_20.rebuy import record_rebuy_evaluation
from trading_ai.first_20.storage import read_json, write_json


def _ack(rt: Path) -> None:
    write_json(
        P_OPERATOR_ACK,
        {"acknowledged_at_iso": datetime.now(timezone.utc).isoformat()},
        runtime_root=rt,
    )


def _base_trade(n: int, *, result: str = "win", pnl: float = 2.0, avenue: str = "A") -> dict:
    return {
        "trade_id": f"ft{n}",
        "result": result,
        "payout_dollars": pnl,
        "avenue_id": avenue,
        "gate_id": "gate_a",
        "strategy_id": "strat_a",
        "market": f"M{n}",
    }


def test_twenty_clean_trades_pass_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    for i in range(20):
        r = process_closed_trade(_base_trade(i), {"execution_close_reconciliation": {}}, runtime_root=tmp_path)
        assert r.get("status") == "recorded"
    truth = read_json(P_TRUTH, runtime_root=tmp_path) or {}
    assert truth.get("phase_status") == PhaseStatus.PASSED_READY_FOR_NEXT_PHASE.value
    assert int(truth.get("trades_completed") or 0) == 20


def test_logging_failure_trade4_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pause requires 3 consecutive logging failures — trades 2,3,4 carry LOGGING_FAILURE."""
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    process_closed_trade(_base_trade(0), {}, runtime_root=tmp_path)
    process_closed_trade(_base_trade(1), {}, runtime_root=tmp_path, extra={"failure_codes": ["LOGGING_FAILURE"]})
    process_closed_trade(_base_trade(2), {}, runtime_root=tmp_path, extra={"failure_codes": ["LOGGING_FAILURE"]})
    process_closed_trade(_base_trade(3), {}, runtime_root=tmp_path, extra={"failure_codes": ["LOGGING_FAILURE"]})
    pause = read_json("data/control/first_20_pause_reason.json", runtime_root=tmp_path) or {}
    assert pause.get("paused") is True


def test_duplicate_guard_trade7_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    for i in range(6):
        process_closed_trade(_base_trade(i), {}, runtime_root=tmp_path)
    process_closed_trade(
        _base_trade(6),
        {},
        runtime_root=tmp_path,
        extra={"failure_codes": ["DUPLICATE_GUARD"], "duplicate_guard_failure": True},
    )
    pause = read_json("data/control/first_20_pause_reason.json", runtime_root=tmp_path) or {}
    assert pause.get("paused") is True
    assert "duplicate_guard_failure_recorded" in (pause.get("reasons") or [])


def test_emergency_brake_trade3_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    for i in range(2):
        process_closed_trade(_base_trade(i), {}, runtime_root=tmp_path)
    process_closed_trade(
        _base_trade(2),
        {},
        runtime_root=tmp_path,
        extra={"emergency_brake_triggered": True},
    )
    pause = read_json(P_PAUSE_REASON, runtime_root=tmp_path) or {}
    assert pause.get("paused") is True


def test_negative_expectancy_clean_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge weak / negative sample; execution rows still clean."""
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    for i in range(16):
        process_closed_trade(
            _base_trade(i, result="loss", pnl=-1.0),
            {},
            runtime_root=tmp_path,
        )
    edge = read_json("data/control/first_20_edge_quality.json", runtime_root=tmp_path) or {}
    execq = read_json("data/control/first_20_execution_quality.json", runtime_root=tmp_path) or {}
    assert edge.get("pass") is False
    assert execq.get("pass") is True


def test_clean_execution_weak_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    for i in range(14):
        process_closed_trade(_base_trade(i, result="loss", pnl=-0.5), {}, runtime_root=tmp_path)
    edge = read_json("data/control/first_20_edge_quality.json", runtime_root=tmp_path) or {}
    assert edge.get("pass") is False


def test_rebuy_before_log_completion_pauses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    record_rebuy_evaluation(
        rebuy_allowed=False,
        block_reason="test",
        any_attempt=True,
        any_before_log_completion=True,
        any_before_exit_truth=False,
        runtime_root=tmp_path,
    )
    process_closed_trade(_base_trade(0), {}, runtime_root=tmp_path)
    truth = read_json(P_TRUTH, runtime_root=tmp_path) or {}
    assert truth.get("phase_status") == PhaseStatus.PAUSED_REVIEW_REQUIRED.value


def test_avenue_adapter_metrics_universal_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    _ack(tmp_path)
    activate_diagnostic_phase(runtime_root=tmp_path)
    process_closed_trade(
        _base_trade(0, avenue="C"),
        {},
        runtime_root=tmp_path,
        extra={
            "avenue_metrics": {"tastytrade_specific": {"margin": 1.0}},
        },
    )
    from trading_ai.first_20.constants import P_DIAGNOSTICS
    from trading_ai.first_20.storage import read_jsonl

    rows = read_jsonl(P_DIAGNOSTICS, runtime_root=tmp_path)
    assert rows[0].get("avenue_id") == "C"
    assert rows[0].get("avenue_metrics", {}).get("tastytrade_specific")


def test_operator_ack_required_for_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_20_MAX_DRAWDOWN_USD", "10000")
    activate_diagnostic_phase(runtime_root=tmp_path)
    for i in range(20):
        process_closed_trade(_base_trade(i), {}, runtime_root=tmp_path)
    pd = read_json("data/control/first_20_pass_decision.json", runtime_root=tmp_path) or {}
    assert pd.get("passed") is False
    assert "operator_truth_artifacts_not_refreshed" in (pd.get("exact_fail_reasons") or [])
