"""Orchestration: switch-live policy, B/C default false, artifact writers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def _minimal_lock(rt: Path) -> None:
    from trading_ai.control.system_execution_lock import save_system_execution_lock

    save_system_execution_lock(
        {
            "system_locked": True,
            "ready_for_live_execution": True,
            "gate_a_enabled": True,
            "gate_b_enabled": False,
            "safety_checks": {
                "policy_aligned": True,
                "capital_truth_valid": True,
                "artifacts_writing": True,
                "supabase_connected": True,
            },
        },
        runtime_root=rt,
    )


def test_b_c_switch_live_false_by_default(rt: Path) -> None:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    _minimal_lock(rt)
    (rt / "data/control").mkdir(parents=True, exist_ok=True)
    (rt / "data/control/go_no_go_decision.json").write_text(
        json.dumps({"ready_for_first_5_trades": True, "ready_for_micro_validation": True}),
        encoding="utf-8",
    )
    (rt / "data/control/execution_mirror_results.json").write_text(
        json.dumps({"ok": True}), encoding="utf-8"
    )
    (rt / "data/control/operator_live_confirmation.json").write_text(
        json.dumps({"confirmed": True}), encoding="utf-8"
    )

    ok_b, bl_b, _ = compute_avenue_switch_live_now("B", runtime_root=rt)
    ok_c, bl_c, _ = compute_avenue_switch_live_now("C", runtime_root=rt)
    assert ok_b is False
    assert ok_c is False
    assert any("independent" in x or "scaffold" in x for x in bl_b + bl_c)


def test_a_requires_operator_when_strict(rt: Path) -> None:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    _minimal_lock(rt)
    (rt / "data/control").mkdir(parents=True, exist_ok=True)
    (rt / "data/control/go_no_go_decision.json").write_text(
        json.dumps({"ready_for_first_5_trades": True}), encoding="utf-8"
    )
    (rt / "data/control/execution_mirror_results.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    ok, blockers, _ = compute_avenue_switch_live_now("A", runtime_root=rt)
    assert ok is False
    assert any("operator" in b.lower() or "confirmation" in b.lower() for b in blockers)


def test_rebuy_requires_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.orchestration.rebuy_eligibility import evaluate_rebuy_eligibility

    ev = evaluate_rebuy_eligibility(
        prior_round_trip_finalized=True,
        logging_succeeded=False,
        reconciliation_ok_or_classified=True,
        governance_recheck_ok=True,
        adaptive_recheck_ok=True,
        failsafe_halted=False,
        duplicate_would_block=False,
        avenue_cooldown_active=False,
    )
    assert ev.rebuy_allowed is False
    assert "logging_required_before_rebuy" in ev.reason_codes


def test_write_orchestration_artifacts(rt: Path) -> None:
    from trading_ai.orchestration.orchestration_truth import write_all_orchestration_artifacts

    _minimal_lock(rt)
    (rt / "data/control").mkdir(parents=True, exist_ok=True)
    (rt / "data/control/go_no_go_decision.json").write_text(json.dumps({}), encoding="utf-8")
    write_all_orchestration_artifacts(runtime_root=rt)
    assert (rt / "data/control/avenue_orchestration_truth.json").is_file()
    assert (rt / "data/control/execution_loop_truth.json").is_file()
