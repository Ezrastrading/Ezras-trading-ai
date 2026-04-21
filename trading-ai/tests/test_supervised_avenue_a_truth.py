"""Supervised Avenue A truth layer — logic only; does not assert production live proof."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.first_20.constants import PhaseStatus
from trading_ai.orchestration.supervised_avenue_a_truth import (
    _runtime_root_matches_proof,
    append_supervised_trade_log_line,
    build_daemon_enable_readiness_after_supervised,
    compute_first_20_fields_for_supervised_daemon,
    load_supervised_log_records,
    refresh_supervised_daemon_truth_chain,
    rollup_supervised_session,
    strict_full_proof_from_disk,
    write_avenue_a_supervised_live_truth,
)


def _full_gate_style_proof(*, trade_id: str, root: Path) -> dict:
    return {
        "FINAL_EXECUTION_PROVEN": True,
        "execution_success": True,
        "coinbase_order_verified": True,
        "databank_written": True,
        "supabase_synced": True,
        "governance_logged": True,
        "packet_updated": True,
        "scheduler_stable": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": [],
        "trade_id": trade_id,
        "runtime_root": str(root),
    }


def test_strict_full_proof_accepts_realistic_matrix() -> None:
    p = _full_gate_style_proof(trade_id="t1", root=Path("/tmp/rt"))
    ok, why = strict_full_proof_from_disk(p)
    assert ok is True
    assert why == "ok"


def test_partial_failure_not_strict() -> None:
    p = _full_gate_style_proof(trade_id="t1", root=Path("/tmp/rt"))
    p["partial_failure_codes"] = ["x"]
    ok, why = strict_full_proof_from_disk(p)
    assert ok is False


def test_runtime_root_mismatch() -> None:
    root = Path("/a/b")
    g = _full_gate_style_proof(trade_id="t1", root=Path("/other"))
    ok, why = _runtime_root_matches_proof(root, g)
    assert ok is False
    assert "mismatch" in why


def test_rollup_consecutive_clean_supervised() -> None:
    recs = [
        {
            "source": "supervised_operator_session",
            "outcome_class": "clean_full_proof",
            "trade_id": "a",
        },
        {
            "source": "supervised_operator_session",
            "outcome_class": "clean_full_proof",
            "trade_id": "b",
        },
    ]
    os.environ["EZRAS_SUPERVISED_CLEAN_TRADES_FOR_PROVEN"] = "2"
    r = rollup_supervised_session(recs)
    assert r["consecutive_clean_supervised_trades"] == 2
    assert r["system_tidy_enough_for_daemon_enable_review"] is True


def test_rollup_excludes_daemon_rows_from_supervised_tidy() -> None:
    recs = [
        {"source": "supervised_operator_session", "outcome_class": "clean_full_proof"},
        {"source": "avenue_a_daemon_cycle", "outcome_class": "clean_full_proof"},
    ]
    os.environ["EZRAS_SUPERVISED_CLEAN_TRADES_FOR_PROVEN"] = "2"
    r = rollup_supervised_session(recs)
    assert r["total_supervised_trades"] == 1
    assert r["consecutive_clean_supervised_trades"] == 1


def test_rollup_includes_daemon_when_ledger_sources_allow() -> None:
    recs = [
        {"source": "supervised_operator_session", "outcome_class": "clean_full_proof"},
        {"source": "avenue_a_daemon_cycle", "outcome_class": "clean_full_proof"},
    ]
    os.environ["EZRAS_SUPERVISED_CLEAN_TRADES_FOR_PROVEN"] = "2"
    r = rollup_supervised_session(
        recs,
        ledger_sources=("supervised_operator_session", "avenue_a_daemon_cycle"),
    )
    assert r["total_supervised_trades"] == 2
    assert r["system_tidy_enough_for_daemon_enable_review"] is True


def test_supervised_live_runtime_proven_with_daemon_only_clean_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_SUPERVISED_CLEAN_TRADES_FOR_PROVEN", "2")
    (tmp_path / "execution_proof").mkdir(parents=True)
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "execution_proof" / "live_execution_validation.json").write_text(
        json.dumps(_full_gate_style_proof(trade_id="d1", root=tmp_path)),
        encoding="utf-8",
    )
    append_supervised_trade_log_line(
        runtime_root=tmp_path,
        record={
            "source": "avenue_a_daemon_cycle",
            "outcome_class": "clean_full_proof",
            "trade_id": "d0",
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    append_supervised_trade_log_line(
        runtime_root=tmp_path,
        record={
            "source": "avenue_a_daemon_cycle",
            "outcome_class": "clean_full_proof",
            "trade_id": "d1",
            "timestamp": "2026-01-01T00:01:00Z",
        },
    )
    out = write_avenue_a_supervised_live_truth(runtime_root=tmp_path)
    assert out.get("supervised_live_runtime_proven") is True
    assert out.get("runtime_root_match") is True


def test_refresh_supervised_daemon_truth_chain_orders_daemon_before_supervised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    calls: list[str] = []

    def fake_daemon(**_k: object) -> dict:
        calls.append("daemon")
        return {"ok": True}

    def fake_supervised(**_k: object) -> dict:
        calls.append("supervised")
        return {"ok": True}

    with monkeypatch.context() as m:
        m.setattr(
            "trading_ai.orchestration.daemon_live_authority.write_all_daemon_live_artifacts",
            fake_daemon,
        )
        m.setattr(
            "trading_ai.orchestration.supervised_avenue_a_truth.write_all_supervised_artifacts_cli",
            fake_supervised,
        )
        refresh_supervised_daemon_truth_chain(runtime_root=tmp_path)
    assert calls == ["daemon", "supervised"]


def test_daemon_enable_stays_false_when_missing_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    out = build_daemon_enable_readiness_after_supervised(runtime_root=tmp_path)
    assert out.get("avenue_a_can_enable_daemon_now") is False
    assert isinstance(out.get("exact_blockers"), list)
    assert len(out.get("exact_blockers") or []) > 0


def test_append_and_reload_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    append_supervised_trade_log_line(
        runtime_root=tmp_path,
        record={"trade_id": "x1", "source": "supervised_operator_session"},
    )
    rows = load_supervised_log_records(tmp_path)
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "x1"


def test_first_20_safe_for_daemon_when_supervised_proven_not_full_program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("EZRAS_FIRST_20_STRICT_FOR_SUPERVISED_DAEMON", raising=False)
    monkeypatch.delenv("EZRAS_FIRST_20_REQUIRED_FOR_LIVE", raising=False)
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "first_20_truth.json").write_text(
        json.dumps({"phase_status": PhaseStatus.ACTIVE_DIAGNOSTIC.value, "ready_for_next_phase": False}),
        encoding="utf-8",
    )
    (ctrl / "first_20_pass_decision.json").write_text(json.dumps({"passed": False}), encoding="utf-8")
    f20_final = {"FIRST_20_READY_FOR_NEXT_PHASE": False, "FIRST_20_SAFE_FOR_LIVE_CAPITAL": False}
    pol = compute_first_20_fields_for_supervised_daemon(
        runtime_root=tmp_path,
        f20_final=f20_final,
        supervised_live_runtime_proven=True,
    )
    assert pol["first_20_safe_enough_for_daemon"] is True
    assert pol["first_20_ready_for_next_phase"] is False


def test_first_20_strict_env_requires_ready_next(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_FIRST_20_STRICT_FOR_SUPERVISED_DAEMON", "true")
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "first_20_truth.json").write_text(
        json.dumps({"phase_status": PhaseStatus.ACTIVE_DIAGNOSTIC.value}),
        encoding="utf-8",
    )
    (ctrl / "first_20_pass_decision.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    pol = compute_first_20_fields_for_supervised_daemon(
        runtime_root=tmp_path,
        f20_final={"FIRST_20_READY_FOR_NEXT_PHASE": False},
        supervised_live_runtime_proven=True,
    )
    assert pol["first_20_safe_enough_for_daemon"] is False


def test_first_20_failed_phase_blocks_despite_supervised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "first_20_truth.json").write_text(
        json.dumps({"phase_status": PhaseStatus.FAILED_REWORK_REQUIRED.value}),
        encoding="utf-8",
    )
    (ctrl / "first_20_pass_decision.json").write_text(json.dumps({"passed": False}), encoding="utf-8")
    pol = compute_first_20_fields_for_supervised_daemon(
        runtime_root=tmp_path,
        f20_final={},
        supervised_live_runtime_proven=True,
    )
    assert pol["first_20_safe_enough_for_daemon"] is False
    assert "first_20_phase_failed_rework" in pol["first_20_hard_blockers"]


def test_write_supervised_truth_requires_runtime_root_in_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "execution_proof").mkdir(parents=True)
    (tmp_path / "data" / "control").mkdir(parents=True)
    bad = dict(_full_gate_style_proof(trade_id="t1", root=tmp_path))
    del bad["runtime_root"]
    (tmp_path / "execution_proof" / "live_execution_validation.json").write_text(json.dumps(bad), encoding="utf-8")
    out = write_avenue_a_supervised_live_truth(runtime_root=tmp_path)
    assert out.get("supervised_live_runtime_proven") is False
    assert "runtime_root" in (out.get("exact_reason_if_false") or "").lower() or out.get("runtime_root_match") is False
