"""Safety / truth / validation lock pass — enforcement, federation, scheduler, fairness, packets."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed
from trading_ai.global_layer.review_confidence import adjust_completeness_for_packet_truth, compute_packet_completeness_score
from trading_ai.global_layer.review_policy import ReviewPolicy
from trading_ai.global_layer.review_scheduler import should_run_eod, should_run_midday, should_run_morning, tick_scheduler
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.trade_truth import load_federated_trades


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
            "joint_review_id": "jr_lock",
            "live_mode_recommendation": mode,
            "review_integrity_state": integrity,
            "generated_at": generated_at,
            "packet_id": "pkt_test",
            "empty": False,
        }
    (gdir / "joint_review_latest.json").write_text(json.dumps(payload), encoding="utf-8")


# --- Enforcement ---


def test_enforcement_off_never_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GOVERNANCE_ORDER_ENFORCEMENT", raising=False)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="paused")
    ok, reason = check_new_order_allowed(venue="kalshi", route="t")
    assert ok is True
    assert "advisory" in reason or "disabled" in reason


def test_enforcement_on_paused_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="paused")
    ok, reason = check_new_order_allowed(venue="coinbase", route="mean_reversion")
    assert ok is False
    assert reason == "joint_review_paused"


def test_enforcement_caution_blocks_under_strict_enforcement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """GOVERNANCE_CAUTION_BLOCK_ENTRIES=true fail-closes on caution when enforcement is on."""
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("GOVERNANCE_CAUTION_BLOCK_ENTRIES", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="caution")
    ok, reason = check_new_order_allowed(venue="kalshi")
    assert ok is False
    assert reason == "joint_review_caution_blocked"


def test_enforcement_missing_joint_always_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strict enforcement: empty/missing joint always denies (GOVERNANCE_MISSING_JOINT_BLOCKS ignored)."""
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.delenv("GOVERNANCE_MISSING_JOINT_BLOCKS", raising=False)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, empty=True)
    ok, reason = check_new_order_allowed(venue="coinbase")
    assert ok is False
    assert "missing_joint" in reason


def test_enforcement_missing_joint_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("GOVERNANCE_MISSING_JOINT_BLOCKS", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, empty=True)
    ok, reason = check_new_order_allowed(venue="coinbase")
    assert ok is False


def test_enforcement_stale_joint_blocks_when_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("GOVERNANCE_STALE_JOINT_BLOCKS", "true")
    monkeypatch.setenv("GOVERNANCE_JOINT_STALE_HOURS", "1")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="normal", generated_at="2020-01-01T12:00:00+00:00")
    ok, reason = check_new_order_allowed(venue="kalshi")
    assert ok is False
    assert "stale" in reason


def test_enforcement_degraded_blocks_when_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="normal", integrity="degraded")
    ok, reason = check_new_order_allowed(venue="kalshi")
    assert ok is False


# --- Federation stress ---


def test_federation_dedupes_memory_trade_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = [
        {"trade_id": "x", "avenue": "coinbase", "net_pnl_usd": 1.0, "logged_at": "2026-04-18T12:00:00+00:00"},
        {"trade_id": "x", "avenue": "coinbase", "net_pnl_usd": 2.0, "logged_at": "2026-04-18T12:01:00+00:00"},
    ]
    ms.save_json("trade_memory.json", tm)
    trades, meta = load_federated_trades(nte_store=ms)
    assert len(trades) == 1
    assert float(trades[0].get("net_pnl_usd") or 0) == 2.0
    assert meta["nte_memory_unique_trade_id_count"] == 1


def test_federation_conflict_net_pnl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "db"))
    from trading_ai.nte.databank.local_trade_store import global_trade_events_path
    from trading_ai.nte.memory.store import MemoryStore

    p = global_trade_events_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "trade_id": "tid_conflict",
            "avenue_id": "A",
            "avenue_name": "coinbase",
            "net_pnl": 99.0,
            "fees_paid": 0.1,
            "timestamp_open": "2026-04-18T10:00:00+00:00",
            "timestamp_close": "2026-04-18T10:05:00+00:00",
        }
    )
    p.write_text(line + "\n", encoding="utf-8")

    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = [
        {
            "trade_id": "tid_conflict",
            "avenue": "coinbase",
            "net_pnl_usd": 1.0,
            "logged_at": "2026-04-18T12:00:00+00:00",
        }
    ]
    ms.save_json("trade_memory.json", tm)

    trades, meta = load_federated_trades(nte_store=ms)
    assert meta["federation_conflict_count"] >= 1
    row = trades[0]
    assert float(row.get("net_pnl_usd") or 0) == 1.0


def test_federation_no_double_count_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = [{"trade_id": "a1", "avenue": "coinbase", "net_pnl_usd": 5.0}]
    ms.save_json("trade_memory.json", tm)
    trades, _meta = load_federated_trades(nte_store=ms)
    assert len(trades) == 1


# --- Scheduler ---


def test_tick_scheduler_uses_fresh_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

    st = ReviewStorage(store=GlobalMemoryStore(root=tmp_path / "shark" / "memory" / "global"))
    st.ensure_review_files()
    with patch("trading_ai.global_layer.review_scheduler.should_run_morning", return_value=False), patch(
        "trading_ai.global_layer.review_scheduler.should_run_midday", return_value=False
    ), patch("trading_ai.global_layer.review_scheduler.should_run_eod", return_value=False):
        out = tick_scheduler(storage=st)
    assert out == []
    tick_path = st.store.path("review_scheduler_ticks.jsonl")
    assert tick_path.is_file()
    lines = tick_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2
    first = json.loads(lines[0])
    assert first["phase"] == "tick_evaluate"
    assert "snap" in first


def test_scheduler_gates_morning_hour(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

    st = ReviewStorage(store=GlobalMemoryStore(root=tmp_path / "shark" / "memory" / "global"))
    st.ensure_review_files()
    pol = ReviewPolicy(enable_morning_review=True)
    sched = st.load_json("review_scheduler_state.json")
    sched["last_morning_ts"] = None
    st.save_json("review_scheduler_state.json", sched)
    with patch("trading_ai.global_layer.review_scheduler._hour_utc", return_value=7):
        assert should_run_morning(pol, st) is True


def test_scheduler_suppress_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

    st = ReviewStorage(store=GlobalMemoryStore(root=tmp_path / "shark" / "memory" / "global"))
    st.ensure_review_files()
    sched = st.load_json("review_scheduler_state.json")
    sched["suppress_all"] = True
    st.save_json("review_scheduler_state.json", sched)
    pol = ReviewPolicy(enable_morning_review=True)
    assert should_run_morning(pol, st) is False
    assert should_run_midday(pol, st, closed_trades_recent=100, shadow_count=100, anomaly_count=1) is False
    assert should_run_eod(pol, st) is False


# --- Packet / completeness ---


def test_packet_completeness_penalized_on_conflicts() -> None:
    pkt = {
        "capital_state": {},
        "avenue_state": {},
        "live_trading_summary": {},
        "risk_summary": {"write_verification_failures": 0},
        "route_summary": {},
        "shadow_exploration_summary": {},
        "goal_state": {},
        "lesson_state": {},
        "review_context_rank": {},
        "packet_truth": {
            "limitations": ["a", "b"],
            "federation_conflict_count": 3,
            "field_quality_summary": {"slippage_coverage_label": "missing_or_thin", "net_pnl_coverage_label": "partial_unknown_net"},
            "avenue_representation": {"kalshi": {"representation": "missing"}},
        },
    }
    base = compute_packet_completeness_score(pkt)
    adj = adjust_completeness_for_packet_truth(pkt, base)
    assert adj < base


# --- Coinbase adapter payload (import-time) ---


def test_coinbase_adapter_builds_valid_trade_id() -> None:
    from trading_ai.nte.databank.coinbase_close_adapter import coinbase_nt_close_to_databank_raw

    pos = {"id": "pos_123", "opened_ts": 1_000_000.0, "strategy": "mean_reversion", "entry_regime": "trend"}
    record = {"net_pnl_usd": 1.0, "duration_sec": 60.0}
    raw = coinbase_nt_close_to_databank_raw(pos, record, exit_reason="take_profit")
    assert raw["trade_id"] == "pos_123"
    assert raw["avenue_name"] == "coinbase"
