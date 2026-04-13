"""Account risk bucket state + thresholds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.automation import risk_bucket as rb


def _write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_fresh_system_normal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    assert rb.get_account_risk_bucket() == "NORMAL"


def test_reduced_two_losses_in_last_three(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_state(
        rb.risk_state_path(),
        {
            "version": 1,
            "equity_index": 98.0,
            "peak_equity_index": 100.0,
            "recent_results": ["win", "loss", "loss"],
            "processed_close_ids": [],
        },
    )
    assert rb.get_account_risk_bucket() == "REDUCED"


def test_reduced_drawdown_over_five_pct(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_state(
        rb.risk_state_path(),
        {
            "version": 1,
            "equity_index": 94.0,
            "peak_equity_index": 100.0,
            "recent_results": ["win", "win", "win"],
            "processed_close_ids": [],
        },
    )
    # dd = 6%
    assert rb.get_account_risk_bucket() == "REDUCED"


def test_blocked_four_losses_in_five(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_state(
        rb.risk_state_path(),
        {
            "version": 1,
            "equity_index": 90.0,
            "peak_equity_index": 100.0,
            "recent_results": ["loss", "loss", "loss", "loss", "win"],
            "processed_close_ids": [],
        },
    )
    assert rb.get_account_risk_bucket() == "BLOCKED"


def test_blocked_drawdown_over_ten_pct(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_state(
        rb.risk_state_path(),
        {
            "version": 1,
            "equity_index": 88.0,
            "peak_equity_index": 100.0,
            "recent_results": ["win"],
            "processed_close_ids": [],
        },
    )
    assert rb.get_account_risk_bucket() == "BLOCKED"


def test_recovery_to_normal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_state(
        rb.risk_state_path(),
        {
            "version": 1,
            "equity_index": 100.0,
            "peak_equity_index": 102.0,
            "recent_results": ["win", "win", "win"],
            "processed_close_ids": [],
        },
    )
    assert rb.get_account_risk_bucket() == "NORMAL"


def test_record_closed_idempotent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {"trade_id": "same-id", "result": "loss", "roi_percent": -1.0}
    rb.record_closed_trade(t)
    rb.record_closed_trade(t)
    st = json.loads(rb.risk_state_path().read_text(encoding="utf-8"))
    assert st["recent_results"].count("loss") == 1


def test_record_closed_updates_equity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rb.record_closed_trade({"trade_id": "a", "result": "win", "roi_percent": 5.0})
    st = json.loads(rb.risk_state_path().read_text(encoding="utf-8"))
    assert st["equity_index"] == pytest.approx(105.0)
    assert st["peak_equity_index"] == pytest.approx(105.0)
