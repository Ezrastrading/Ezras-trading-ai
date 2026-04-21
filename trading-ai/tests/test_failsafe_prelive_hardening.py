"""Failsafe, ledger, recovery, prelive harnesses, taxonomy (mock/staged)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_kill_switch_blocks(rt: Path) -> None:
    from trading_ai.control.system_execution_lock import ensure_system_execution_lock_file
    from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks, write_kill_switch

    ensure_system_execution_lock_file(runtime_root=rt)
    write_kill_switch(True, runtime_root=rt)
    ok, code, _ = run_failsafe_checks(
        FailsafeContext(
            action="place_market_entry",
            avenue_id="coinbase",
            product_id="BTC-USD",
            gate="gate_a",
            quote_notional=50.0,
            base_size=None,
            quote_balances_by_ccy={"USD": 1000.0},
            strategy_id="t",
            trade_id="t1",
            multi_leg=False,
            skip_governance=True,
        ),
        runtime_root=rt,
    )
    assert ok is False
    assert "kill_switch" in code.lower() or "system_kill_switch" in code.lower()


def test_capital_truth_usdc_pair(rt: Path) -> None:
    from trading_ai.runtime.capital_truth import assert_executable_capital_for_product

    ok, _ = assert_executable_capital_for_product(
        "ETH-USDC",
        requested_quote=50.0,
        quote_balances_by_ccy={"USD": 1e6, "USDC": 5.0},
    )
    assert ok is False


def test_ledger_append_has_trade_id(rt: Path) -> None:
    from trading_ai.runtime.trade_ledger import append_trade_ledger_line

    line = append_trade_ledger_line(
        {"execution_status": "test", "validation_status": "test"},
        runtime_root=rt,
    )
    assert line.get("trade_id")


def test_recovery_audit_writes(rt: Path) -> None:
    from trading_ai.runtime.recovery import run_recovery_audit

    out = run_recovery_audit(runtime_root=rt)
    assert "resolution" in out
    p = rt / "data" / "control" / "recovery_log.json"
    assert p.is_file()


def test_error_normalize(rt: Path) -> None:
    from trading_ai.safety.error_taxonomy import ExecutionErrorCode, normalize_error_code

    assert normalize_error_code("timeout") == ExecutionErrorCode.EXECUTION_TIMEOUT.value


def test_live_state_roundtrip(rt: Path) -> None:
    from trading_ai.runtime.live_execution_state import read_live_execution_state, record_execution_step

    record_execution_step(step="unit", avenue="coinbase", gate="gate_a", success=True, runtime_root=rt)
    s = read_live_execution_state(runtime_root=rt)
    assert s.get("last_action") == "unit"


def test_failsafe_multi_leg(rt: Path) -> None:
    from trading_ai.control.system_execution_lock import ensure_system_execution_lock_file
    from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks

    ensure_system_execution_lock_file(runtime_root=rt)
    ok, code, _ = run_failsafe_checks(
        FailsafeContext(
            action="place_market_entry",
            avenue_id="coinbase",
            product_id="BTC-USD",
            gate="gate_a",
            quote_notional=50.0,
            base_size=None,
            quote_balances_by_ccy={"USD": 1000.0},
            strategy_id="t",
            trade_id="t2",
            multi_leg=True,
            skip_governance=True,
        ),
        runtime_root=rt,
    )
    assert ok is False
    assert "multi_leg" in code.lower()


def test_execution_mirror(rt: Path) -> None:
    from trading_ai.prelive.execution_mirror import run as mirror_run

    out = mirror_run(runtime_root=rt)
    assert out.get("ok") is True
    assert (rt / "data/control/execution_mirror_results.json").is_file()


def test_mock_harness_smoke(rt: Path) -> None:
    from trading_ai.prelive.mock_execution_harness import run as harness_run

    harness_run(runtime_root=rt)
    p = rt / "data/control/mock_execution_harness_results.json"
    assert p.is_file()
    j = json.loads(p.read_text(encoding="utf-8"))
    assert len(j.get("scenarios") or []) >= 3


def test_friction_lab_count(rt: Path) -> None:
    from trading_ai.prelive.execution_friction_lab import run as lab_run

    out = lab_run(runtime_root=rt)
    assert len(out["scenarios"]) >= 20


def test_sizing_sandbox(rt: Path) -> None:
    from trading_ai.prelive.sizing_calibration_sandbox import run as sz_run

    out = sz_run(runtime_root=rt)
    assert len(out["rows"]) >= 1


def test_gate_b_staged(rt: Path) -> None:
    from trading_ai.prelive.gate_b_staged_validation import run as gb_run

    gb_run(runtime_root=rt)
    assert (rt / "data/control/gate_b_staged_validation.json").is_file()


def test_operator_audit(rt: Path) -> None:
    from trading_ai.prelive.operator_interpretation_audit import run as op_run

    op_run(runtime_root=rt)
    assert (rt / "data/control/operator_interpretation_audit.json").is_file()


def test_deployment_truth(rt: Path) -> None:
    from trading_ai.prelive.deployment_truth_audit import run as dep_run

    out = dep_run(runtime_root=rt)
    assert "operator_must_confirm" in out


def test_avenue_auto_attach(rt: Path) -> None:
    from trading_ai.prelive.avenue_auto_attach_proof import run as aa_run

    out = aa_run(runtime_root=rt)
    assert out.get("scoped_root_exists") is True


def test_honesty_system_truth(rt: Path) -> None:
    from trading_ai.prelive.honesty_enforcement import run as h_run

    h_run(runtime_root=rt, repo_root=Path(__file__).resolve().parents[2])
    assert (rt / "data/control/system_truth_final.json").is_file()


def test_timing_anomaly(rt: Path) -> None:
    from trading_ai.runtime.timing_guards import record_timing_anomaly

    record_timing_anomaly({"kind": "test"}, runtime_root=rt)
    assert (rt / "data/control/timing_anomalies.json").is_file()


def test_go_no_go_shape(rt: Path) -> None:
    from trading_ai.prelive.execution_mirror import run as mirror_run
    from trading_ai.prelive.go_no_go import run as gng_run

    mirror_run(runtime_root=rt)
    out = gng_run(runtime_root=rt)
    assert "ready_for_first_5_trades" in out
    assert "blockers" in out


def test_prelive_full_smoke(rt: Path) -> None:
    import os

    from trading_ai.prelive import __main__ as prelive_main

    os.environ["EZRAS_RUNTIME_ROOT"] = str(rt)
    prelive_main.main()
    assert (rt / "data/control/prelive_lock_report.json").is_file()
