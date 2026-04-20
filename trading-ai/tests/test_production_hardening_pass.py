"""Production hardening: databank halts, runtime isolation, freeze policy, Gate B writes, discovery, guards."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_databank_write_halt_threshold_and_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.databank.databank_write_halt import (
        configured_write_failure_threshold,
        record_databank_trade_write_outcome,
    )

    assert configured_write_failure_threshold() == 3
    monkeypatch.setenv("SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD", "2")
    assert configured_write_failure_threshold() == 2

    r1 = record_databank_trade_write_outcome(False, "e1", runtime_root=tmp_path, rehearsal_mode=True)
    assert r1["streak"] == 1
    r2 = record_databank_trade_write_outcome(False, "e2", runtime_root=tmp_path, rehearsal_mode=True)
    assert r2["streak"] == 2
    r_ok = record_databank_trade_write_outcome(True, None, runtime_root=tmp_path, rehearsal_mode=True)
    assert r_ok["streak"] == 0


def test_system_guard_isolated_runtime_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.core import system_guard as sg

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(a))
    sg.reset_system_guard_singletons_for_tests()
    ga = sg.get_system_guard()
    ga.halt_now("test_halt_a")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(b))
    sg.reset_system_guard_singletons_for_tests()
    gb = sg.get_system_guard()
    assert not gb.is_trading_halted()


def test_orchestration_freeze_scope_local_skips_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.safety.kill_switch_engine import _resolve_orchestration_freeze_policy

    scope, applied, _ = _resolve_orchestration_freeze_policy(
        severity="CRITICAL",
        freeze_orchestration_on_critical=True,
        orchestration_freeze_scope="local_only",
        avenue_id=None,
    )
    assert scope in ("local_only", "local")
    assert applied is False


def test_gate_b_truth_write_report_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "review").mkdir(parents=True)
    from trading_ai.reports.gate_b_control_truth import write_gate_b_truth_artifacts

    with patch("trading_ai.reports.gate_b_control_truth.gate_b_live_status_report", return_value={}):
        with patch("trading_ai.reports.gate_b_control_truth.audit_trade_event_row_stats", return_value={}):
            with patch("trading_ai.reports.gate_b_control_truth.default_production_pnl_only", return_value=True):
                out = write_gate_b_truth_artifacts(runtime_root=tmp_path)
    wr = out.get("gate_b_truth_write_report") or {}
    assert "compact_write_ok" in wr
    assert tmp_path.joinpath("data", "control", "gate_b_truth_compact.json").is_file()


def test_gate_discovery_idempotent_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_BOT_HIERARCHY_ROOT", str(tmp_path))
    from trading_ai.global_layer.bot_hierarchy.gate_discovery import discover_gate_candidate
    from trading_ai.global_layer.bot_hierarchy.registry import load_hierarchy_state

    o1 = discover_gate_candidate(
        avenue_id="A",
        gate_id="g1",
        strategy_thesis="t",
        edge_hypothesis="e",
        execution_path="p",
        path=tmp_path,
    )
    assert o1.get("idempotent") is False
    o2 = discover_gate_candidate(
        avenue_id="A",
        gate_id="g1",
        strategy_thesis="t",
        edge_hypothesis="e",
        execution_path="p",
        path=tmp_path,
    )
    assert o2.get("idempotent") is True
    st = load_hierarchy_state(tmp_path)
    assert len(st.get("gate_candidates") or []) == 1


def test_universal_live_guard_coinbase_allowed() -> None:
    from trading_ai.safety.universal_live_guard import evaluate_universal_live_guard

    ok, reason, _ = evaluate_universal_live_guard("coinbase", "gate_b", fail_closed=True)
    assert ok and reason == "ok"


def test_universal_live_guard_unknown_denied() -> None:
    from trading_ai.safety.universal_live_guard import evaluate_universal_live_guard

    ok, reason, _ = evaluate_universal_live_guard("unknown_venue_x", "gate_z", fail_closed=True)
    assert ok is False


def test_ssl_subprocess_smoke_decision_shape() -> None:
    from trading_ai.runtime_checks.ssl_subprocess_smoke import deployment_subprocess_smoke_decision

    d = deployment_subprocess_smoke_decision()
    assert d.get("truth_version")
    assert "recommend_subprocess_smoke" in d


def test_controlled_live_readiness_has_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "deployment").mkdir(parents=True)
    (tmp_path / "data" / "deployment" / "supabase_schema_readiness.json").write_text(
        json.dumps({"schema_ready": False, "error_classification": "test"}),
        encoding="utf-8",
    )
    from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report

    with patch("trading_ai.deployment.controlled_live_readiness.run_check_env", return_value={"coinbase_credentials_ok": False}):
        with patch(
            "trading_ai.deployment.controlled_live_readiness.build_autonomous_operator_path",
            return_value={"active_blockers": [], "can_arm_autonomous_now": False},
        ):
            out = build_controlled_live_readiness_report(runtime_root=tmp_path, write_artifact=False)
    assert "human_summary" in out
    assert out.get("truth_version") == "controlled_live_readiness_v2"
