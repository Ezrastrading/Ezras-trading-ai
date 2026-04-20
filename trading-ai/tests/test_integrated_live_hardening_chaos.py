"""
Integrated deterministic chaos rehearsal — temp roots, no network, no capital.

Combines: databank halt streak + recovery, runtime-root isolation, Gate B write report,
gate discovery idempotency, universal guard deny, venue-family freeze, controlled-live-readiness wiring truth.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def test_integrated_live_hardening_chaos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD", "2")
    orch = tmp_path / "orchestration_kill_switch.json"
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_kill_switch.orchestration_kill_switch_path",
        lambda: orch,
    )

    from trading_ai.core import system_guard as sg
    from trading_ai.global_layer.bot_hierarchy.gate_discovery import discover_gate_candidate
    from trading_ai.global_layer.registry_cross_link import build_registry_cross_link_report
    from trading_ai.nte.databank.databank_write_halt import record_databank_trade_write_outcome
    from trading_ai.reports.gate_b_control_truth import write_gate_b_truth_artifacts
    from trading_ai.safety.universal_live_guard import evaluate_universal_live_guard
    from trading_ai.safety.kill_switch_engine import activate_halt

    # 1–2: databank streak + recovery
    r1 = record_databank_trade_write_outcome(False, "e1", runtime_root=tmp_path, rehearsal_mode=True)
    r2 = record_databank_trade_write_outcome(False, "e2", runtime_root=tmp_path, rehearsal_mode=True)
    assert r2["streak"] >= 2
    r_ok = record_databank_trade_write_outcome(True, None, runtime_root=tmp_path, rehearsal_mode=True)
    assert r_ok["streak"] == 0

    # 3: runtime root isolation
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(a))
    sg.reset_system_guard_singletons_for_tests()
    sg.get_system_guard().halt_now("chaos_a")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(b))
    sg.reset_system_guard_singletons_for_tests()
    assert sg.get_system_guard().is_trading_halted() is False
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    # 4: Gate B compact + write report (mock heavy deps)
    for _p in (
        tmp_path / "data" / "control",
        tmp_path / "data" / "reports",
        tmp_path / "data" / "review",
    ):
        _p.mkdir(parents=True, exist_ok=True)
    with patch("trading_ai.reports.gate_b_control_truth.gate_b_live_status_report", return_value={}):
        with patch("trading_ai.reports.gate_b_control_truth.audit_trade_event_row_stats", return_value={}):
            with patch("trading_ai.reports.gate_b_control_truth.default_production_pnl_only", return_value=True):
                with patch(
                    "trading_ai.reports.gate_b_global_halt_truth.write_gate_b_global_halt_truth_artifacts",
                    side_effect=RuntimeError("verbose_global_halt_failed"),
                ):
                    gb_out = write_gate_b_truth_artifacts(runtime_root=tmp_path)
    wr = gb_out.get("gate_b_truth_write_report") or {}
    assert wr.get("compact_write_ok") is True
    assert wr.get("verbose_write_ok") is False
    assert any("gate_b_global_halt_truth" in str(x) for x in (wr.get("verbose_failures") or []))

    # 5: gate discovery idempotent
    monkeypatch.setenv("EZRAS_BOT_HIERARCHY_ROOT", str(tmp_path / "hier"))
    hroot = tmp_path / "hier"
    hroot.mkdir()
    d1 = discover_gate_candidate(
        avenue_id="chaos",
        gate_id="g1",
        strategy_thesis="t",
        edge_hypothesis="e",
        execution_path="p",
        path=hroot,
    )
    d2 = discover_gate_candidate(
        avenue_id="chaos",
        gate_id="g1",
        strategy_thesis="t",
        edge_hypothesis="e",
        execution_path="p",
        path=hroot,
    )
    assert d1.get("idempotent") is False
    assert d2.get("idempotent") is True

    # 6: universal guard deny
    ok, reason, _ = evaluate_universal_live_guard("zzz_unregistered", "gate_a", fail_closed=True)
    assert ok is False and "unregistered" in reason

    # 7: venue-family freeze via activate_halt
    monkeypatch.setenv("EZRAS_KILL_SWITCH_FREEZE_ORCHESTRATION", "1")
    ah = activate_halt(
        "MANUAL_OPERATOR_HALT",
        source_component="chaos",
        severity="CRITICAL",
        immediate_action_required="x",
        orchestration_freeze_scope="venue_family",
        venue_family_id="spot_crypto",
        runtime_root=tmp_path,
        broadcast_system_guard=False,
        rehearsal_mode=False,
    )
    assert (ah.get("truth") or {}).get("detail", {}).get("resolved_freeze_scope") == "venue_family"

    # 8: readiness wiring + blockers structure
    (tmp_path / "data" / "deployment").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "deployment" / "supabase_schema_readiness.json").write_text(
        '{"schema_ready": false}', encoding="utf-8"
    )
    from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report

    with patch("trading_ai.deployment.controlled_live_readiness.run_check_env", return_value={"coinbase_credentials_ok": False}):
        with patch(
            "trading_ai.deployment.controlled_live_readiness.build_autonomous_operator_path",
            return_value={"active_blockers": ["chaos_blocker"], "can_arm_autonomous_now": False},
        ):
            rep = build_controlled_live_readiness_report(runtime_root=tmp_path, write_artifact=False)
    sw = rep.get("safety_wiring_truth") or {}
    assert sw.get("gate_discovery_idempotency", {}).get("status") == "wired_and_runtime_observable"
    assert "not_ready_for_autonomous_live_because" in (rep.get("operator_summary") or {})

    # 9: registry cross-link artifact (no crash)
    out_x = build_registry_cross_link_report(runtime_root=tmp_path)
    assert out_x.get("truth_version") == "registry_cross_link_v1"
