"""Tests for trader visibility reporting (CSV + daily/weekly summaries)."""

from __future__ import annotations

from datetime import date

import pytest

from trading_ai.reporting.daily_summary import rebuild_daily_summary
from trading_ai.reporting.paths import (
    daily_summary_json_path,
    daily_summary_txt_path,
    trades_clean_csv_path,
    weekly_summary_json_path,
    weekly_summary_txt_path,
)
from trading_ai.reporting.trade_ledger import append_clean_trade_row
from trading_ai.reporting.weekly_summary import rebuild_weekly_summary


def _minimal_trade(**kwargs):
    base = {
        "timestamp_close": "2026-04-19T15:00:00+00:00",
        "timestamp_open": "2026-04-19T14:00:00+00:00",
        "avenue_name": "coinbase",
        "asset": "BTC-USD",
        "instrument_kind": "spot",
        "side": "buy",
        "actual_entry_price": 100.0,
        "actual_exit_price": 101.0,
        "base_qty": 0.5,
        "fees_paid": 0.1,
        "gross_pnl": 0.5,
        "net_pnl": 0.4,
        "expected_edge_bps": 10.0,
        "execution_quality_score": 0.9,
        "latency_ms": 50.0,
        "hold_seconds": 3600.0,
        "regime": "trend",
        "edge_id": "e1",
        "edge_status_at_trade": "validated",
    }
    base.update(kwargs)
    return base


@pytest.fixture
def rt_root(tmp_path, monkeypatch):
    root = tmp_path / "ez"
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    return root


def test_append_three_trades_csv_rows(rt_root):
    for i, pnl in enumerate([10.0, -3.0, 5.0]):
        ok = append_clean_trade_row(
            _minimal_trade(
                net_pnl=pnl,
                gross_pnl=pnl + 0.1,
                timestamp_close=f"2026-04-19T1{i}:00:00+00:00",
            )
        )
        assert ok is True
    p = trades_clean_csv_path()
    text = p.read_text(encoding="utf-8")
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    assert len(lines) == 4  # header + 3


def test_daily_summary_pnl_and_win_rate(rt_root):
    append_clean_trade_row(_minimal_trade(net_pnl=10.0, gross_pnl=10.2))
    append_clean_trade_row(_minimal_trade(net_pnl=-4.0, gross_pnl=-3.8))
    append_clean_trade_row(_minimal_trade(net_pnl=10.0, gross_pnl=10.1))
    d = rebuild_daily_summary(as_of=date(2026, 4, 19))
    assert d["total_trades"] == 3
    assert pytest.approx(d["total_pnl"], rel=1e-6) == 16.0
    assert d["win_rate_pct"] == 66.67  # rounded in JSON payload
    assert daily_summary_json_path().is_file()
    txt = daily_summary_txt_path().read_text(encoding="utf-8")
    assert "TOTAL PNL:" in txt
    assert "DATE: 2026-04-19" in txt


def test_weekly_summary_aggregation(rt_root):
    # Same ISO week 2026-W16 (April 13-19 2026 — verify week for Apr 19)
    append_clean_trade_row(
        _minimal_trade(
            net_pnl=5.0,
            timestamp_close="2026-04-18T12:00:00+00:00",
            timestamp_open="2026-04-18T11:00:00+00:00",
        )
    )
    append_clean_trade_row(
        _minimal_trade(
            net_pnl=-2.0,
            timestamp_close="2026-04-19T12:00:00+00:00",
            timestamp_open="2026-04-19T11:00:00+00:00",
        )
    )
    w = rebuild_weekly_summary(as_of=date(2026, 4, 19))
    assert w["total_trades"] == 2
    assert pytest.approx(w["total_pnl"], rel=1e-6) == 3.0
    assert weekly_summary_json_path().is_file()
    wtxt = weekly_summary_txt_path().read_text(encoding="utf-8")
    assert "WEEK:" in wtxt
    assert "TOTAL PNL:" in wtxt


def test_missing_required_skips_row(rt_root):
    bad = {"timestamp_close": "2026-04-19T12:00:00+00:00"}
    assert append_clean_trade_row(bad) is False
    p = trades_clean_csv_path()
    if p.is_file():
        n = len(p.read_text(encoding="utf-8").strip().splitlines())
        assert n <= 1


def test_no_crash_if_csv_missing(rt_root):
    assert not trades_clean_csv_path().is_file()
    d = rebuild_daily_summary(as_of=date(2026, 1, 1))
    assert d["total_trades"] == 0
    assert daily_summary_txt_path().is_file()
