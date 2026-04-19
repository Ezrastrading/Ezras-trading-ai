"""Tests for read-only market intelligence and non-authoritative strategy research."""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.market_intelligence import market_intelligence_engine as mi
from trading_ai.market_intelligence.market_intelligence_engine import get_active_markets
from trading_ai.strategy_research import research_execution_guard as reg
from trading_ai.strategy_research.research_execution_guard import (
    RESEARCH_EXECUTION_BAN_MSG,
    assert_strategy_research_read_allowed,
)
from trading_ai.strategy_research.strategy_research_engine import (
    iter_research_log_entries,
    research_log_path,
    run_strategy_research_cycle,
)


def test_get_active_markets_structure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    monkeypatch.setattr(
        mi,
        "_coinbase_intel",
        lambda: [
            {"symbol": "BTC-USD", "price": 50000.0, "volume": 100.0, "volatility": 0.02},
            {"symbol": "ETH-USD", "price": 3000.0, "volume": 200.0, "volatility": 0.03},
        ],
    )
    monkeypatch.setattr(
        mi,
        "_kalshi_intel",
        lambda: [
            {"market": "BTC > 70k today", "odds": 0.35, "volume": 1200.0},
            {"market": "S&P > 5000", "odds": 0.62, "volume": 500.0},
        ],
    )
    monkeypatch.setattr(
        mi,
        "_options_intel_stub",
        lambda: [{"symbol": "SPY", "type": "call", "volume": None, "open_interest": None}],
    )

    out = get_active_markets(force_snapshot=True, snapshot_min_interval_sec=0.0)
    assert "coinbase" in out and "kalshi" in out and "options" in out
    assert isinstance(out["coinbase"], list) and len(out["coinbase"]) >= 1
    assert isinstance(out["kalshi"], list) and len(out["kalshi"]) >= 1
    assert isinstance(out["options"], list) and out["options"][0].get("symbol") == "SPY"

    snap = tmp_path / "market_intelligence" / "active_markets_snapshot.json"
    assert snap.is_file()
    loaded = json.loads(snap.read_text(encoding="utf-8"))
    assert loaded["schema"] == "active_markets_v1"


def test_run_strategy_research_writes_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    mi_dir = tmp_path / "market_intelligence"
    mi_dir.mkdir(parents=True, exist_ok=True)
    (mi_dir / "active_markets_snapshot.json").write_text(
        json.dumps({"coinbase": [], "kalshi": [], "options": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trading_ai.strategy_research.strategy_research_engine.get_active_markets",
        lambda **k: {"coinbase": [], "kalshi": [], "options": []},
    )

    res = run_strategy_research_cycle(force_stub=True, refresh_market_snapshot=False, write_daily_summary=True)
    assert res["entries_written"] == 2
    logp = research_log_path()
    assert logp.is_file()
    lines = logp.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert row["validated"] is False
        assert row["source"] in ("gpt", "claude")
        assert row["confidence"] in ("LOW", "MEDIUM", "HIGH")
        assert "hypothesis" in row


def test_iter_research_log_entries_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    logp = tmp_path / "strategy_research" / "research_log.jsonl"
    logp.parent.mkdir(parents=True, exist_ok=True)
    logp.write_text(json.dumps({"hypothesis": "x", "source": "gpt"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "trading_ai.strategy_research.strategy_research_engine.research_log_path",
        lambda: logp,
    )
    rows = list(iter_research_log_entries())
    assert len(rows) == 1


def test_guard_raises_from_coinbase_engine_stack() -> None:
    self_fr = types.SimpleNamespace(
        f_code=types.SimpleNamespace(co_filename="/safe/tests/test_ok.py"),
    )
    bad_fr = types.SimpleNamespace(
        f_code=types.SimpleNamespace(
            co_filename="/proj/trading_ai/nte/execution/coinbase_engine.py",
        ),
    )
    fake_stack = [
        types.SimpleNamespace(frame=self_fr),
        types.SimpleNamespace(frame=bad_fr),
    ]
    with patch.object(reg.inspect, "stack", return_value=fake_stack):
        with pytest.raises(Exception, match=RESEARCH_EXECUTION_BAN_MSG):
            assert_strategy_research_read_allowed()


def test_execution_modules_do_not_import_strategy_research() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "trading_ai"
    needle = "strategy_research"
    offenders: list[str] = []
    for rel in (
        "nte/execution/coinbase_engine.py",
        "shark/execution_live.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        if needle in text:
            offenders.append(rel)
    assert not offenders, f"execution modules must not reference {needle}: {offenders}"


def test_governance_package_has_no_strategy_research_refs() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "trading_ai" / "governance"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "strategy_research" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root.parent)))
    assert not offenders


def test_guard_raises_for_governance_module_name() -> None:
    class _GovMod:
        __name__ = "trading_ai.governance.audit_chain"

    gov_frame = types.SimpleNamespace(
        f_code=types.SimpleNamespace(co_filename="/tmp/neutral_path.py"),
    )
    fake_stack = [
        types.SimpleNamespace(frame=types.SimpleNamespace(f_code=types.SimpleNamespace(co_filename="/t/ok.py"))),
        types.SimpleNamespace(frame=gov_frame),
    ]
    with patch.object(reg.inspect, "stack", return_value=fake_stack):
        with patch.object(reg.inspect, "getmodule", return_value=_GovMod()):
            with pytest.raises(Exception, match=RESEARCH_EXECUTION_BAN_MSG):
                assert_strategy_research_read_allowed()
