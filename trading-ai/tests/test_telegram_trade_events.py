"""Telegram trade alert formatting (operator feed)."""

from __future__ import annotations

import json

import pytest

from trading_ai.automation.telegram_trade_events import format_trade_closed_message, format_trade_placed_message


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
    }
    msg = format_trade_placed_message(t)
    assert "Ezras — TRADE OPEN" in msg
    assert "Market: Fed decision before June" in msg
    assert "Ticker: FED-JUNE-25" in msg
    assert "Side: BUY_YES" in msg
    assert "Risk Mode: NORMAL" in msg
    assert "Size: $2,500.00" in msg
    assert "Risk:" in msg and "% of account" in msg
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
    }
    msg = format_trade_closed_message(t)
    assert "Ezras — TRADE CLOSED" in msg
    assert "Market: Fed decision before June" in msg
    assert "Ticker: FED-JUNE-25" in msg
    assert "Side: BUY_NO" in msg
    assert "Risk Mode:" in msg
    assert "Exit: 0.88" in msg
    assert "Gross P&L: $125.00" in msg
    assert "Net P&L: $120.50" in msg
    assert "ROI:" in msg and "%" in msg
    assert "Execution Cost: $4.50" in msg
    assert "Strategy: macro" in msg
    assert "Close Reason:" in msg
    assert "target_hit" in msg
    assert "Trade ID: t-closed-full" in msg
    assert "2026-04-13 18:00 UTC" in msg


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


def test_blocked_risk_shows_trading_disabled(isolated_runtime) -> None:
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
    assert "Risk Mode: BLOCKED" in msg
    assert "Trading Disabled" in msg
