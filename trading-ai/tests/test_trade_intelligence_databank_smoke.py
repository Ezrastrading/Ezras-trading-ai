"""Smoke tests for Trade Intelligence Databank (Section 9)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from trading_ai.nte.databank.local_trade_store import (
    global_trade_events_path,
    global_trade_scores_path,
    path_daily_summary,
    path_databank_health,
    path_write_verification,
)
from trading_ai.nte.databank.query_helpers import (
    avenue_comparison,
    first_n_trades,
    trades_by_avenue,
)
from trading_ai.nte.databank.trade_intelligence_databank import TradeIntelligenceDatabank


def _minimal_trade(trade_id: str, avenue_id: str, avenue_name: str) -> dict:
    return {
        "trade_id": trade_id,
        "avenue_id": avenue_id,
        "avenue_name": avenue_name,
        "asset": "BTC-USD",
        "strategy_id": "test_strat",
        "route_chosen": "A",
        "route_a_score": 0.72,
        "route_b_score": 0.61,
        "rejected_route": "B",
        "rejected_reason": "lower_score",
        "regime": "trend",
        "timestamp_open": "2026-04-18T10:00:00+00:00",
        "timestamp_close": "2026-04-18T10:15:00+00:00",
        "expected_edge_bps": 12.0,
        "net_pnl": 5.0,
        "gross_pnl": 5.5,
        "fees_paid": 0.5,
        "entry_slippage_bps": 2.0,
        "exit_slippage_bps": 1.0,
        "maker_taker": "maker",
    }


def test_one_closed_trade_full_local_record(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    raw = _minimal_trade("tid_smoke_1", "A", "coinbase")
    out = TradeIntelligenceDatabank().process_closed_trade(raw)
    assert out["ok"] is True
    assert out["stages"]["local_raw_event"] is True
    assert out["stages"]["local_score_record"] is True
    lines = global_trade_events_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["trade_id"] == "tid_smoke_1"
    assert rec["execution_score"] is not None
    assert "trade_quality_score" in rec
    scores = json.loads(global_trade_scores_path().read_text(encoding="utf-8"))
    assert "tid_smoke_1" in scores.get("by_trade_id", {})


def test_supabase_upsert_called(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    calls: list = []

    def capture(row) -> bool:
        calls.append(row)
        return True

    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        capture,
    )
    raw = _minimal_trade("tid_supa", "A", "coinbase")
    TradeIntelligenceDatabank().process_closed_trade(raw)
    assert len(calls) == 1
    assert calls[0]["trade_id"] == "tid_supa"
    assert calls[0]["route_a_score"] == 0.72
    assert calls[0]["route_b_score"] == 0.61


def test_scores_computed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    raw = _minimal_trade("tid_sc", "B", "kalshi")
    out = TradeIntelligenceDatabank().process_closed_trade(raw)
    s = out["scores"]
    assert 0 <= s["execution_score"] <= 100
    assert 0 <= s["edge_score"] <= 100
    assert 0 <= s["discipline_score"] <= 100
    assert 0 <= s["trade_quality_score"] <= 100


def test_summaries_update(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    TradeIntelligenceDatabank().process_closed_trade(_minimal_trade("tid_sum", "A", "coinbase"))
    daily = json.loads(path_daily_summary().read_text(encoding="utf-8"))
    assert daily.get("rollups")


def test_failed_supabase_logged(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: False,
    )
    out = TradeIntelligenceDatabank().process_closed_trade(_minimal_trade("tid_fail", "A", "coinbase"))
    assert out["ok"] is False
    assert "supabase_upsert_failed" in (out.get("errors") or [])
    ver = json.loads(path_write_verification().read_text(encoding="utf-8"))
    assert ver.get("last", {}).get("partial_failure") is True
    health = json.loads(path_databank_health().read_text(encoding="utf-8"))
    assert health.get("status") == "degraded"


def test_duplicate_trade_id_no_second_row(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    t = _minimal_trade("tid_dup", "A", "coinbase")
    assert TradeIntelligenceDatabank().process_closed_trade(t)["ok"] is True
    r2 = TradeIntelligenceDatabank().process_closed_trade(t)
    assert r2["ok"] is False
    lines = global_trade_events_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


@pytest.mark.parametrize(
    "aid,name",
    [
        ("A", "coinbase"),
        ("B", "kalshi"),
        ("C", "tastytrade"),
    ],
)
def test_avenue_tagging(aid: str, name: str, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    tid = f"tid_{aid}_av"
    TradeIntelligenceDatabank().process_closed_trade(_minimal_trade(tid, aid, name))
    comp = avenue_comparison(path=global_trade_events_path())
    assert aid in comp
    assert trades_by_avenue(aid, path=global_trade_events_path())[0]["avenue_name"] == name


def test_route_fields_persist(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    raw = _minimal_trade("tid_route", "A", "coinbase")
    TradeIntelligenceDatabank().process_closed_trade(raw)
    line = json.loads(global_trade_events_path().read_text(encoding="utf-8").strip().splitlines()[0])
    assert line["route_a_score"] == 0.72
    assert line["route_b_score"] == 0.61
    assert line["rejected_route"] == "B"


def test_first_twenty_query(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    for i in range(25):
        TradeIntelligenceDatabank().process_closed_trade(
            _minimal_trade(f"tid_{i:03d}", "A", "coinbase"),
        )
    first = first_n_trades(20, path=global_trade_events_path())
    assert len(first) == 20
    assert first[0]["trade_id"] == "tid_000"


def test_goal_and_learning_hooks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trading_ai.nte.databank.trade_intelligence_databank.upsert_trade_event",
        lambda row: True,
    )
    TradeIntelligenceDatabank().process_closed_trade(_minimal_trade("tid_hook", "A", "coinbase"))
    goal = tmp_path / "goal_progress_snapshot.json"
    assert goal.exists()
    hooks = tmp_path / "research_learning_hooks.jsonl"
    assert hooks.exists()
    assert "tid_hook" in hooks.read_text(encoding="utf-8")
