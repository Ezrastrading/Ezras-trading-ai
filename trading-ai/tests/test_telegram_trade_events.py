"""Telegram trade alert formatting (operator feed)."""

from __future__ import annotations

import json

import pytest

from trading_ai.automation.telegram_trade_events import (
    format_trade_closed_message,
    format_trade_placed_message,
)


@pytest.fixture
def isolated_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))


def test_open_full_payload(isolated_runtime) -> None:
    t = {
        "trade_id": "t-open-full",
        "timestamp": "2026-04-12T15:30:45+00:00",
        "market": "Fed decision before June",
        "ticker": "FED-JUNE-25",
        "position": "YES",
        "entry_price": 0.42,
        "capital_allocated": 2500.0,
        "signal_score": 8,
        "expected_value": 0.04,
        "event_name": "FED-JUNE-25",
        "market_category": "macro",
        "reasoning_text": "Curve pricing lags desk baseline; size capped per regime.",
        "position_sizing_meta": {
            "requested_size": 2500.0,
            "approved_size": 2500.0,
            "sizing_multiplier": 1.0,
            "raw_bucket": "NORMAL",
            "effective_bucket": "NORMAL",
            "bucket": "NORMAL",
            "bucket_fallback_applied": False,
            "approval_status": "APPROVED",
            "reason": "risk_bucket_ok",
        },
        "risk_bucket_at_open": "NORMAL",
    }
    msg = format_trade_placed_message(t)
    assert "Ezras — TRADE OPEN" in msg
    assert "Market: Fed decision before June" in msg
    assert "Ticker: FED-JUNE-25" in msg
    assert "Side: BUY_YES" in msg
    assert "Risk Mode: NORMAL" in msg
    assert "Trading Status: ACTIVE" in msg
    assert "Requested Size: $2,500.00" in msg
    assert "Approved Size: $2,500.00" in msg
    assert "Size Adjustment:" not in msg
    assert "Risk: 5.0% of account" in msg
    assert "Entry: 0.42" in msg
    assert "Signal Score: 8" in msg
    assert "Strategy: macro" in msg
    assert "Target / EV:" in msg
    assert "Reason:" in msg
    assert "Curve pricing lags desk baseline" in msg
    assert "Trade ID: t-open-full" in msg
    assert "2026-04-12 15:30 UTC" in msg
    assert "status: open" not in msg.lower()


def test_closed_full_payload(isolated_runtime) -> None:
    t = {
        "trade_id": "t-closed-full",
        "timestamp": "2026-04-13T18:00:00+00:00",
        "market": "Fed decision before June",
        "ticker": "FED-JUNE-25",
        "position": "NO",
        "exit_price": 0.88,
        "result": "win",
        "roi_percent": 6.25,
        "capital_allocated": 2000.0,
        "gross_pnl_dollars": 125.0,
        "net_pnl_dollars": 120.5,
        "total_execution_cost_dollars": 4.5,
        "event_name": "FED-JUNE-25",
        "market_category": "macro",
        "exit_reason": "target_hit",
        "reasoning_text": "Bank book flat; took profit into liquidity.",
        "risk_bucket_at_open": "NORMAL",
    }
    msg = format_trade_closed_message(t)
    assert "Ezras — TRADE CLOSED / PAYOUT" in msg
    assert "Market: Fed decision before June" in msg
    assert "Ticker: FED-JUNE-25" in msg
    assert "Side: BUY_NO" in msg
    assert "Risk Mode After Close:" in msg
    assert "Exit: 0.88" in msg
    assert "P&L (net): $120.50" in msg
    assert "P&L (gross): $125.00" in msg
    assert "Payout amount: $2,120.50" in msg
    assert "ROI:" in msg and "%" in msg
    assert "Execution Cost: $4.50" in msg
    assert "Strategy: macro" in msg
    assert "Close Reason:" in msg
    assert "target_hit" in msg
    assert "Trade ID: t-closed-full" in msg
    assert "2026-04-13 18:00 UTC" in msg


def test_closed_shows_bucket_change_when_different(isolated_runtime) -> None:
    t = {
        "trade_id": "t-bc",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "market": "M",
        "position": "YES",
        "exit_price": 0.9,
        "result": "win",
        "roi_percent": 1.0,
        "capital_allocated": 10.0,
        "risk_bucket_at_open": "NORMAL",
    }
    from trading_ai.automation import risk_bucket as rb

    p = rb.risk_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "equity_index": 88.0,
                "peak_equity_index": 100.0,
                "recent_results": ["win"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )
    msg = format_trade_closed_message(t)
    assert "Risk Mode After Close: BLOCKED" in msg
    assert "Bucket Change: NORMAL → BLOCKED" in msg


def test_open_minimal_payload_omits_optionals(isolated_runtime) -> None:
    t = {
        "trade_id": "t-min",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "market": "X",
        "position": "YES",
        "entry_price": 0.5,
        "capital_allocated": 100.0,
        "signal_score": 5,
    }
    msg = format_trade_placed_message(t)
    assert "Ezras — TRADE OPEN" in msg
    assert "Risk Mode: NORMAL" in msg
    assert "Trading Status: ACTIVE" in msg
    assert "Requested Size: $100.00" in msg
    assert "Approved Size: $100.00" in msg
    assert "Risk: 5.0% of account" in msg
    assert "Ticker:" not in msg
    assert "Reason:" not in msg
    assert "Trade ID: t-min" in msg


def test_ticker_falls_back_to_event_name(isolated_runtime) -> None:
    t = {
        "trade_id": "t1",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "market": "Some market",
        "event_name": "KXBTC-24DEC-T50000",
        "position": "NO",
        "entry_price": 0.33,
        "capital_allocated": 50.0,
        "signal_score": 6,
        "expected_value": 0.01,
    }
    msg = format_trade_placed_message(t)
    assert "Ticker: KXBTC-24DEC-T50000" in msg


def test_reduced_open_truthful_risk(isolated_runtime) -> None:
    t = {
        "trade_id": "tr",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "market": "M",
        "position": "YES",
        "entry_price": 0.5,
        "capital_allocated": 100.0,
        "signal_score": 5,
        "position_sizing_meta": {
            "requested_size": 100.0,
            "approved_size": 50.0,
            "sizing_multiplier": 0.5,
            "raw_bucket": "REDUCED",
            "effective_bucket": "REDUCED",
            "bucket": "REDUCED",
            "bucket_fallback_applied": False,
            "approval_status": "REDUCED",
            "reason": "risk_bucket_reduction",
            "trading_allowed": True,
            "normalized_at": "2026-01-01T00:00:00+00:00",
            "source": "fixture",
            "repair_applied": False,
            "repair_reason": None,
        },
        "risk_bucket_at_open": "REDUCED",
    }
    msg = format_trade_placed_message(t)
    assert "Risk Mode: REDUCED" in msg
    assert "Trading Status: ACTIVE" in msg
    assert "Requested Size: $100.00" in msg
    assert "Approved Size: $50.00" in msg
    assert "Size Adjustment: Reduced 50%" in msg
    assert "Requested Risk Basis:" in msg
    assert "Approved Risk Basis:" in msg
    assert "Risk: 2.5% of account" in msg


def test_blocked_open_shows_zero_risk(isolated_runtime) -> None:
    from trading_ai.automation.risk_bucket import risk_state_path

    p = risk_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "equity_index": 80.0,
                "peak_equity_index": 100.0,
                "recent_results": ["loss", "loss", "loss", "loss", "win"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )
    t = {
        "trade_id": "x",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "market": "M",
        "position": "YES",
        "entry_price": 0.5,
        "capital_allocated": 10.0,
        "signal_score": 5,
    }
    msg = format_trade_placed_message(t)
    assert "Ezras — TRADE BLOCKED" in msg
    assert "TRADE OPEN" not in msg
    assert "Risk Mode: BLOCKED" in msg
    assert "Trading Status: DISABLED" in msg
    assert "Requested Size: $10.00" in msg
    assert "Approved Size: $0.00" in msg
    assert "Block Reason:" in msg
