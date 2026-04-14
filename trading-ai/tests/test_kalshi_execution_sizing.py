"""Kalshi live execution uses position-sizing approved notional → contract count."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_ai.config import Settings
from trading_ai.execution import kalshi_exec
from trading_ai.models.schemas import CandidateMarket, TradeBrief, TradeDecisionView


def _settings(**kw) -> Settings:
    base = Settings()
    return base.model_copy(
        update={
            "kalshi_enabled": True,
            "kalshi_execution_enabled": True,
            "kalshi_default_order_size": 10,
            **kw,
        }
    )


def _market() -> CandidateMarket:
    return CandidateMarket(
        market_id="pm-1",
        question="Q",
        raw={"kalshi_ticker": "KXTEST"},
        source_platform="polymarket",
    )


def _brief() -> TradeBrief:
    return TradeBrief(
        market_id="pm-1",
        market_question="Q",
        uncertainty="low",
        edge_hypothesis="e",
        signal_score=8,
    )


def _decision() -> TradeDecisionView:
    return TradeDecisionView(
        edge_pct_yes=1.0,
        edge_pct_no=0.0,
        action="BUY_YES",
        explanation="x",
    )


def test_kalshi_submits_contracts_equal_to_approved_notional_normal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 100.0,
                "peak_equity_index": 100.0,
                "recent_results": [],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.get_market_price", lambda _s, _t: 0.5)
    placed: list[int] = []

    def _place(_settings, _ticker, _side, size: int):
        placed.append(size)
        return {"ok": True, "order": {"order_id": "oid-1"}}

    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.place_order", _place)

    out = kalshi_exec.execute_buy_on_kalshi(_settings(), _market(), _brief(), _decision())
    assert out.get("kalshi_order_id") == "oid-1"
    assert placed == [10]
    # requested = 10 * 0.5 = 5, approved = 5, contracts = floor(5/0.5) = 10
    assert out.get("submitted_contracts") == 10
    assert out.get("approved_size_dollars") == pytest.approx(5.0)


def test_kalshi_reduced_submits_half_contracts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 98.0,
                "peak_equity_index": 100.0,
                "recent_results": ["win", "loss", "loss"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.get_market_price", lambda _s, _t: 0.5)
    placed: list[int] = []

    def _place(_settings, _ticker, _side, size: int):
        placed.append(size)
        return {"ok": True, "order": {"order_id": "oid-2"}}

    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.place_order", _place)

    out = kalshi_exec.execute_buy_on_kalshi(_settings(), _market(), _brief(), _decision())
    assert placed == [5]
    assert out.get("submitted_contracts") == 5
    assert out.get("approved_size_dollars") == pytest.approx(2.5)


def test_kalshi_blocked_no_place_order(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
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

    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.get_market_price", lambda _s, _t: 0.5)
    mock_place = MagicMock(return_value={"ok": True, "order": {"order_id": "x"}})
    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.place_order", mock_place)

    out = kalshi_exec.execute_buy_on_kalshi(_settings(), _market(), _brief(), _decision())
    assert out.get("kalshi_execution_error") == "sizing_blocked_or_zero_approved"
    mock_place.assert_not_called()
    log = tmp_path / "logs" / "execution_submission_log.md"
    assert log.is_file()
    assert "submission_aborted" in log.read_text(encoding="utf-8")


def test_kalshi_zero_contracts_after_approve_no_place_order(monkeypatch, tmp_path: Path) -> None:
    """REDUCED half of a small dollar request can round to < 1 contract at this price — no submit."""
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 98.0,
                "peak_equity_index": 100.0,
                "recent_results": ["win", "loss", "loss"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.get_market_price", lambda _s, _t: 0.95)
    mock_place = MagicMock(return_value={"ok": True, "order": {"order_id": "x"}})
    monkeypatch.setattr("trading_ai.execution.kalshi_exec.kalshi_client.place_order", mock_place)

    out = kalshi_exec.execute_buy_on_kalshi(
        _settings(kalshi_default_order_size=1),
        _market(),
        _brief(),
        _decision(),
    )
    assert out.get("kalshi_execution_error") == "approved_notional_below_one_contract"
    mock_place.assert_not_called()
