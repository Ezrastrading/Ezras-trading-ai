"""Runtime-proof harness: governance touch points, scheduler repetition, federation scale."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
from trading_ai.global_layer.kalshi_execution_mirror import append_kalshi_execution_mirror, load_mirror_rows
from trading_ai.global_layer.review_scheduler import tick_scheduler
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.shark.execution_live import submit_order
from trading_ai.shark.models import ExecutionIntent, HuntType


def _write_joint(
    gdir: Path,
    *,
    mode: str = "normal",
    empty: bool = False,
    integrity: str = "full",
    generated_at: str = "2099-01-01T12:00:00+00:00",
) -> None:
    if empty:
        payload = {"schema_version": "1.0", "artifact": "joint", "empty": True, "generated_at": generated_at}
    else:
        payload = {
            "joint_review_id": "jr_rt",
            "live_mode_recommendation": mode,
            "review_integrity_state": integrity,
            "generated_at": generated_at,
            "packet_id": "pkt_rt",
            "empty": False,
        }
    (gdir / "joint_review_latest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_governance_full_audit_has_required_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="normal")
    ok, reason, audit = check_new_order_allowed_full(
        venue="coinbase",
        operation="test",
        route="n/a",
        intent_id="intent-1",
    )
    assert ok is True
    assert audit["enforcement_enabled"] is True
    assert audit["venue"] == "coinbase"
    assert audit["route"] == "n/a"
    assert audit["intent_id"] == "intent-1"
    assert "live_mode" in audit
    assert "review_integrity_state" in audit
    assert "joint_stale" in audit
    assert audit["reason_code"] == reason


def test_submit_order_kalshi_invokes_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="normal")

    calls: list = []

    def _spy(*args: object, **kwargs: object) -> tuple:
        calls.append((args, kwargs))
        return True, "joint_review_normal", {"allowed": True}

    monkeypatch.setattr(
        "trading_ai.global_layer.governance_order_gate.check_new_order_allowed_full",
        _spy,
    )

    class _FakeKalshi:
        def place_order(self, **kwargs: object):
            return MagicMock(
                order_id="oid1",
                success=True,
                status="filled",
                filled_price=0.5,
                filled_size=1.0,
                timestamp=0.0,
                outlet="kalshi",
                raw={},
            )

    monkeypatch.setattr("trading_ai.shark.outlets.kalshi.KalshiClient", lambda: _FakeKalshi())

    intent = ExecutionIntent(
        outlet="kalshi",
        market_id="KXTEST-99",
        side="yes",
        shares=1,
        expected_price=0.5,
        edge_after_fees=0.1,
        stake_fraction_of_capital=0.01,
        notional_usd=10.0,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.KALSHI_CONVERGENCE],
        source="test",
        meta={"strategy_key": "s1"},
    )
    r = submit_order(intent)
    assert r.success is True
    assert len(calls) == 1


def test_submit_order_coinbase_blocked_when_gate_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="paused")

    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")

    intent = ExecutionIntent(
        outlet="coinbase",
        market_id="BTC-USD",
        side="buy",
        shares=1,
        expected_price=50000.0,
        edge_after_fees=0.01,
        stake_fraction_of_capital=0.01,
        notional_usd=10.0,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.CRYPTO_SCALP],
        source="test",
        meta={"product_id": "BTC-USD"},
    )
    r = submit_order(intent)
    assert r.success is False
    assert r.status == "governance_blocked"


def test_kalshi_mirror_federates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.ensure_defaults()
    append_kalshi_execution_mirror(
        intent_summary={"x": 1},
        order_id="ord_mirror_1",
        success=True,
        raw_status="filled",
    )
    rows = load_mirror_rows()
    assert len(rows) >= 1
    trades, meta = load_federated_trades(nte_store=ms)
    assert meta.get("kalshi_execution_mirror_only_count", 0) >= 1
    ids = {str(t.get("trade_id")) for t in trades}
    assert any(x.startswith("kxm_ord_mirror_1_") for x in ids)


def test_federation_many_trades_no_double_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True, exist_ok=True)
    mir = tmp_path / "shark" / "memory" / "global" / "kalshi_execution_mirror.jsonl"
    mir.parent.mkdir(parents=True, exist_ok=True)
    mir.write_text("", encoding="utf-8")
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = [{"trade_id": f"t{i}", "avenue": "coinbase", "net_pnl_usd": 1.0} for i in range(300)]
    ms.save_json("trade_memory.json", tm)
    trades, meta = load_federated_trades(nte_store=ms)
    assert len(trades) == 300
    assert meta["merged_trade_count"] == 300


def test_tick_scheduler_repeated_evaluate_complete_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    st.ensure_review_files()
    monkeypatch.setattr(
        "trading_ai.global_layer.review_scheduler.should_run_morning",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.review_scheduler.should_run_midday",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.review_scheduler.should_run_eod",
        lambda *a, **k: False,
    )
    for _ in range(40):
        tick_scheduler(storage=st)
    p = st.store.path("review_scheduler_ticks.jsonl")
    assert p.is_file()
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 80
    phases = []
    for ln in lines:
        rec = json.loads(ln)
        phases.append(rec.get("phase"))
    assert phases.count("tick_evaluate") == 40
    assert phases.count("tick_complete") == 40


def test_enforcement_unknown_mode_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.delenv("GOVERNANCE_UNKNOWN_MODE_BLOCKS", raising=False)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    (gdir / "joint_review_latest.json").write_text(
        json.dumps(
            {
                "joint_review_id": "jr_u",
                "live_mode_recommendation": "not_a_real_mode",
                "review_integrity_state": "full",
                "generated_at": "2099-01-01T12:00:00+00:00",
                "empty": False,
            }
        ),
        encoding="utf-8",
    )
    ok, reason, _ = check_new_order_allowed_full(venue="coinbase", route="r")
    assert ok is False
    assert reason == "unknown_live_mode_fail_closed"
