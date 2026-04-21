"""Tests for daily diagnosis, knowledge ontology, learning memory, and command center."""

from __future__ import annotations

import json
from datetime import date
import pytest

from trading_ai.review.daily_diagnosis import (
    build_diagnosis,
    recommend_risk_mode,
    run_daily_diagnosis,
)
from trading_ai.knowledge import (
    describe_avenue,
    explain_how_loss_happened,
    explain_how_profit_is_made,
    validate_ontology_internal,
)
from trading_ai.learning.improvement_loop import ingest_daily_diagnosis, link_recommendation_outcome
from trading_ai.learning.trading_memory import load_trading_memory
from trading_ai.control.command_center import compose_snapshot, gather_command_center_inputs, run_command_center_snapshot
from trading_ai.control.paths import command_center_report_path, command_center_snapshot_path


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    root = tmp_path / "rt"
    root.mkdir(parents=True)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    db = root / "databank"
    db.mkdir(parents=True)
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(db))
    (root / "shark" / "state").mkdir(parents=True, exist_ok=True)
    (root / "data" / "reality").mkdir(parents=True, exist_ok=True)
    (root / "shark" / "nte" / "memory").mkdir(parents=True, exist_ok=True)
    (root / "databank" / "organism").mkdir(parents=True, exist_ok=True)
    return root


def test_recommend_risk_mode_bad_day_lowers():
    m = {
        "rolling_expectancy": -0.5,
        "consecutive_losses": 4,
        "fee_to_pnl_ratio": 0.5,
        "slippage_consuming_edge": True,
        "discipline_deteriorating": True,
        "anomalies_rising": True,
        "validated_edge_post_fee_positive": False,
        "drawdown_fraction": 0.2,
        "execution_healthy": False,
        "trade_count": 10,
        "high_confidence_sample": False,
    }
    r = recommend_risk_mode(m)
    assert r["risk_mode"] == "lower_risk"
    assert r["size_multiplier_recommendation"] < 1.0


def test_recommend_risk_mode_strong_validated_hold_or_raise():
    m = {
        "rolling_expectancy": 0.2,
        "consecutive_losses": 0,
        "fee_to_pnl_ratio": 0.05,
        "slippage_consuming_edge": False,
        "discipline_deteriorating": False,
        "anomalies_rising": False,
        "validated_edge_post_fee_positive": True,
        "drawdown_fraction": 0.05,
        "execution_healthy": True,
        "trade_count": 40,
        "high_confidence_sample": True,
    }
    r = recommend_risk_mode(m)
    assert r["risk_mode"] in ("hold_risk", "raise_risk")


def test_recommend_risk_mode_anomaly_heavy_lowers():
    m = {
        "rolling_expectancy": 0.01,
        "consecutive_losses": 0,
        "fee_to_pnl_ratio": 0.1,
        "slippage_consuming_edge": False,
        "discipline_deteriorating": False,
        "anomalies_rising": True,
        "validated_edge_post_fee_positive": True,
        "drawdown_fraction": 0.05,
        "execution_healthy": True,
        "trade_count": 15,
        "high_confidence_sample": False,
    }
    r = recommend_risk_mode(m)
    assert r["risk_mode"] == "lower_risk"


def test_recommend_risk_mode_mixed_hold():
    m = {
        "rolling_expectancy": 0.02,
        "consecutive_losses": 1,
        "fee_to_pnl_ratio": 0.15,
        "slippage_consuming_edge": False,
        "discipline_deteriorating": False,
        "anomalies_rising": False,
        "validated_edge_post_fee_positive": True,
        "drawdown_fraction": 0.08,
        "execution_healthy": True,
        "trade_count": 12,
        "high_confidence_sample": False,
    }
    r = recommend_risk_mode(m)
    assert r["risk_mode"] == "hold_risk"


def test_ontology_consistent():
    assert not validate_ontology_internal()
    assert "edge" in __import__("trading_ai.knowledge.trading_ontology", fromlist=["TRADING_ONTOLOGY"]).TRADING_ONTOLOGY


def test_avenue_descriptions():
    assert describe_avenue("coinbase")["class"] == "spot"
    assert "payout" in describe_avenue("kalshi") or "binary" in json.dumps(describe_avenue("kalshi"))
    assert describe_avenue("options")["class"] == "options"


def test_profit_explanations_differ():
    assert "Spot" in explain_how_profit_is_made("coinbase", {}) or "spot" in explain_how_profit_is_made(
        "coinbase", {}
    ).lower()
    assert "prediction" in explain_how_profit_is_made("kalshi", {}).lower() or "settle" in explain_how_profit_is_made(
        "kalshi", {}
    ).lower()
    assert "option" in explain_how_profit_is_made("options", {}).lower()


def test_learning_persists(isolated_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(isolated_runtime))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(isolated_runtime / "databank"))
    d = {
        "date": "2026-04-19",
        "metrics": {
            "venue_performance": {"coinbase": {"net_pnl": -5, "trades": 2, "wins": 0, "win_rate": 0}},
            "avg_slippage_bps": 50,
            "anomaly_count": 6,
        },
        "risk_recommendation": {"risk_mode": "lower_risk"},
        "key_problems": ["x"],
        "key_strengths": ["y"],
        "recommended_actions": [],
    }
    ingest_daily_diagnosis(d)
    mem = load_trading_memory()
    assert mem.get("repeated_mistakes") or mem.get("edge_improvements")

    link_recommendation_outcome("rec1", success=True, detail="tightened sizing")
    mem2 = load_trading_memory()
    assert mem2.get("recommendations_that_worked")


def test_command_center_graceful_missing(isolated_runtime, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(isolated_runtime))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(isolated_runtime / "databank"))
    inputs = gather_command_center_inputs()
    snap = compose_snapshot(inputs)
    assert snap.get("timestamp")
    assert "system_health" in snap
    assert isinstance(snap.get("alerts"), list)


def test_governance_block_reflected(isolated_runtime, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(isolated_runtime))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(isolated_runtime / "databank"))
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    gdir = isolated_runtime / "shark" / "memory" / "global"
    gdir.mkdir(parents=True, exist_ok=True)
    gdir.joinpath("joint_review_latest.json").write_text(
        json.dumps(
            {
                "joint_review_id": "jr_block_test",
                "live_mode_recommendation": "paused",
                "review_integrity_state": "full",
                "generated_at": "2026-04-19T12:00:00+00:00",
                "empty": False,
            }
        ),
        encoding="utf-8",
    )
    inputs = gather_command_center_inputs()
    snap = compose_snapshot(inputs)
    assert snap["governance_state"]["trade_entry_blocked"] is True


def test_command_center_halted_flag(isolated_runtime, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(isolated_runtime))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(isolated_runtime / "databank"))
    halt = isolated_runtime / "shark" / "state" / "system_trading_halt.json"
    halt.write_text(json.dumps({"reason": "test_halt"}), encoding="utf-8")
    inputs = gather_command_center_inputs()
    assert inputs["halt_present"] is True
    snap = compose_snapshot(inputs)
    assert snap["system_health"]["halted"] is True
    crit = [a for a in snap["alerts"] if a.get("level") == "CRITICAL"]
    assert any("halt" in (a.get("message") or "").lower() for a in crit)


def test_command_center_edge_counts(isolated_runtime, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(isolated_runtime))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(isolated_runtime / "databank"))
    reg = isolated_runtime / "databank" / "edge_registry.json"
    reg.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "edges": [
                    {"edge_id": "e1", "status": "validated", "avenue": "coinbase"},
                    {"edge_id": "e2", "status": "candidate", "avenue": "kalshi"},
                ],
            }
        ),
        encoding="utf-8",
    )
    inputs = gather_command_center_inputs()
    snap = compose_snapshot(inputs)
    assert snap["edge_state"]["counts_by_status"].get("validated") == 1


def test_command_center_writes_report(isolated_runtime, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(isolated_runtime))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(isolated_runtime / "databank"))
    run_command_center_snapshot(write_files=True)
    assert command_center_snapshot_path().is_file()
    rp = command_center_report_path()
    assert rp.is_file()
    txt = rp.read_text(encoding="utf-8")
    assert "COMMAND CENTER" in txt


def test_build_diagnosis_minimal():
    d = date(2026, 4, 19)
    diagnosis = build_diagnosis(
        as_of=d,
        databank_events=[
            {"timestamp_close": "2026-04-19T12:00:00Z", "net_pnl_usd": 1.0, "venue": "coinbase"},
            {"timestamp_close": "2026-04-19T13:00:00Z", "net_pnl_usd": -0.5, "venue": "coinbase"},
        ],
        edge_truth_summary={"edges": {}},
        edge_registry_edges=[],
        halt_present=False,
    )
    assert diagnosis["date"] == "2026-04-19"
    assert diagnosis["metrics"]["total_trades"] == 2
