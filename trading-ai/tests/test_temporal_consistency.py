"""Temporal consistency baselines."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.governance.temporal_consistency import (
    build_temporal_summary,
    record_temporal_event,
    record_verdict_sample,
)


def test_record_temporal_event_appends_sample(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    record_temporal_event("operator_registry_activated", source="test")
    s = build_temporal_summary()
    assert s["windows"]["1d"]["sample_count"] >= 1


def test_temporal_trend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    for _ in range(5):
        record_verdict_sample("ALIGNED", rule_triggered="t", source="test")
    for _ in range(5):
        record_verdict_sample("DRIFTING", rule_triggered="d", source="test")
    s = build_temporal_summary()
    assert "windows" in s
    assert s["windows"]["1d"]["sample_count"] >= 10
    assert s["overall_trend"] in (
        "degrading",
        "oscillating",
        "watch",
        "stable",
        "insufficient_data",
    )
