"""Organism lock report generators (isolation, parity, goals, Kalshi)."""

from __future__ import annotations

import json

import pytest

from trading_ai.runtime_proof.avenue_parity_report import build_avenue_parity_report, write_avenue_parity_report
from trading_ai.runtime_proof.databank_isolation_report import run_databank_isolation_validation, write_databank_isolation_report
from trading_ai.runtime_proof.goal_progress_reports import write_goal_progress_artifacts
from trading_ai.runtime_proof.kalshi_readiness_report import build_kalshi_parity_status, write_kalshi_parity_status


def test_databank_isolation_report_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True, exist_ok=True)
    r = run_databank_isolation_validation(runtime_root=tmp_path)
    assert r.get("ok") is True
    assert r.get("databank_root_source")
    p = write_databank_isolation_report(tmp_path)
    assert p.is_file()
    assert json.loads(p.read_text(encoding="utf-8"))["schema"] == "databank_isolation_report_v1"


def test_avenue_parity_report_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True, exist_ok=True)
    rep = build_avenue_parity_report()
    assert rep.get("schema") == "avenue_parity_report_v1"
    assert isinstance(rep.get("avenues"), list)
    p = write_avenue_parity_report(tmp_path)
    assert json.loads(p.read_text(encoding="utf-8"))["merged_trade_count"] is not None


def test_kalshi_status_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True, exist_ok=True)
    s = build_kalshi_parity_status()
    assert s.get("schema") == "kalshi_parity_status_v1"
    write_kalshi_parity_status(tmp_path)


def test_coinbase_only_kalshi_expected_emits_fairness_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    monkeypatch.setenv("COINBASE_ENABLED", "true")
    monkeypatch.setenv("KALSHI_API_KEY", "test_key")
    (tmp_path / "databank").mkdir(parents=True, exist_ok=True)
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.ensure_defaults()
    ms.append_trade({"trade_id": "c1", "avenue": "coinbase", "net_pnl_usd": 1.0, "route_bucket": "x"})
    rep = build_avenue_parity_report(nte_store=ms)
    assert any("parity_gap" in w for w in rep.get("fairness_warnings", []))


def test_goal_progress_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True, exist_ok=True)
    paths = write_goal_progress_artifacts(tmp_path)
    for p in paths.values():
        assert p.is_file()
