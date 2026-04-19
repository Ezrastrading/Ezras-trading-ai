"""Execution monitor: slippage / latency degradation flags."""

from __future__ import annotations

from collections import deque

import pytest

from trading_ai.monitoring.execution_monitor import (
    ExecutionSample,
    detect_degradation,
    record_execution,
)


def test_detect_degradation_latency_spike(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_MONITOR_LATENCY_BASELINE_MS", "50")
    hist = deque([48.0, 52.0, 49.0])
    sample = ExecutionSample(
        trade_id="t1",
        expected_fill_price=100.0,
        actual_fill_price=100.0,
        slippage=0.0,
        latency_ms=200.0,
    )
    flags = detect_degradation(sample, latency_history=hist)
    assert flags["latency_bad"] is True
    assert flags["flagged"] is True


def test_slippage_flag_when_threshold_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_MONITOR_SLIPPAGE_THRESHOLD_BPS", "5")
    monkeypatch.setenv("EXECUTION_MONITOR_LATENCY_BASELINE_MS", "1000")
    sample = ExecutionSample(
        trade_id="t2",
        expected_fill_price=100.0,
        actual_fill_price=100.06,
        slippage=0.06,
        latency_ms=10.0,
    )
    flags = detect_degradation(sample, latency_history=deque([10.0, 10.0]))
    assert flags["slippage_bad"] is True
    assert flags["flagged"] is True


def test_record_execution_persists_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EXECUTION_MONITOR_SLIPPAGE_THRESHOLD_BPS", "10000")
    monkeypatch.setenv("EXECUTION_MONITOR_LATENCY_BASELINE_MS", "1000")
    sample = ExecutionSample(
        trade_id="t3",
        expected_fill_price=50.0,
        actual_fill_price=50.0,
        slippage=0.0,
        latency_ms=5.0,
    )
    from trading_ai.monitoring import execution_monitor as em

    p = em.execution_metrics_path()
    if p.is_file():
        p.unlink()
    record_execution(sample, latency_history=deque([5.0, 5.0]), append_metrics=True)
    assert p.is_file()
    line = p.read_text(encoding="utf-8").strip().splitlines()[0]
    import json

    obj = json.loads(line)
    assert obj["trade_id"] == "t3"
    assert "degradation" in obj
