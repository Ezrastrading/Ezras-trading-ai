"""Deployment micro-validation streak must not trip standard duplicate_trade_window between runs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.control.system_execution_lock import ensure_system_execution_lock_file
from trading_ai.nte.hardening.live_order_guard import deployment_micro_validation_duplicate_isolation_key
from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks


def test_standard_duplicate_still_blocks_second_identical_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE", raising=False)
    ensure_system_execution_lock_file(runtime_root=tmp_path)
    lock = json.loads((tmp_path / "data/control/system_execution_lock.json").read_text())
    lock["gate_a_enabled"] = True
    (tmp_path / "data/control/system_execution_lock.json").write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setenv("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC", "3600")

    ctx1 = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_a",
        quote_notional=10.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
        strategy_id="live",
        trade_id="t1",
        multi_leg=False,
        skip_governance=True,
    )
    ok1, _, _ = run_failsafe_checks(ctx1, runtime_root=tmp_path)
    assert ok1 is True
    ctx2 = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_a",
        quote_notional=11.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
        strategy_id="live",
        trade_id="t2",
        multi_leg=False,
        skip_governance=True,
    )
    ok2, code2, _ = run_failsafe_checks(ctx2, runtime_root=tmp_path)
    assert ok2 is False
    assert "DUPLICATE" in (code2 or "").upper()


def test_gate_a_and_gate_b_do_not_share_duplicate_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same product/action in the same window: different gates → distinct duplicate keys."""
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE", raising=False)
    ensure_system_execution_lock_file(runtime_root=tmp_path)
    lock = json.loads((tmp_path / "data/control/system_execution_lock.json").read_text())
    lock["gate_a_enabled"] = True
    lock["gate_b_enabled"] = True
    (tmp_path / "data/control/system_execution_lock.json").write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setenv("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC", "3600")

    ctx_a = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_a",
        quote_notional=10.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
        strategy_id="live",
        trade_id="ta",
        multi_leg=False,
        skip_governance=True,
    )
    ctx_b = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_b",
        quote_notional=10.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
        strategy_id="live",
        trade_id="tb",
        multi_leg=False,
        skip_governance=True,
    )
    ok_a, _, _ = run_failsafe_checks(ctx_a, runtime_root=tmp_path)
    ok_b, _, _ = run_failsafe_checks(ctx_b, runtime_root=tmp_path)
    assert ok_a is True
    assert ok_b is True


def test_micro_validation_isolation_allows_consecutive_same_product(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ensure_system_execution_lock_file(runtime_root=tmp_path)
    lock = json.loads((tmp_path / "data/control/system_execution_lock.json").read_text())
    lock["gate_a_enabled"] = True
    (tmp_path / "data/control/system_execution_lock.json").write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setenv("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC", "3600")
    monkeypatch.setenv("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE", "1")
    monkeypatch.setenv("EZRAS_MICRO_VALIDATION_SESSION_ID", "sess_test")
    monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "1")

    iso1 = deployment_micro_validation_duplicate_isolation_key()
    assert iso1 == "sess_test_r1"

    ctx1 = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_a",
        quote_notional=10.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
        strategy_id="live",
        trade_id="t1",
        multi_leg=False,
        skip_governance=True,
        validation_duplicate_isolation_key=iso1,
    )
    ok1, _, _ = run_failsafe_checks(ctx1, runtime_root=tmp_path)
    assert ok1 is True

    monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "2")
    iso2 = deployment_micro_validation_duplicate_isolation_key()
    assert iso2 == "sess_test_r2"

    ctx2 = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_a",
        quote_notional=10.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e9, "USDC": 1e9},
        strategy_id="live",
        trade_id="t2",
        multi_leg=False,
        skip_governance=True,
        validation_duplicate_isolation_key=iso2,
    )
    ok2, code2, _ = run_failsafe_checks(ctx2, runtime_root=tmp_path)
    assert ok2 is True, code2


def test_duplicate_proof_fields_when_not_in_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE", raising=False)
    from trading_ai.runtime_proof.live_execution_validation import duplicate_guard_proof_fields_for_live_validation

    d = duplicate_guard_proof_fields_for_live_validation()
    assert d["duplicate_guard_mode"] == "standard"
    assert d["validation_scope_duplicate_isolation_key"] is None


def test_duplicate_proof_fields_under_streak_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE", "1")
    monkeypatch.setenv("EZRAS_MICRO_VALIDATION_SESSION_ID", "abc")
    monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "2")
    from trading_ai.runtime_proof.live_execution_validation import duplicate_guard_proof_fields_for_live_validation

    d = duplicate_guard_proof_fields_for_live_validation()
    assert d["duplicate_guard_mode"] == "deployment_micro_validation_isolated_keys"
    assert d["validation_scope_duplicate_isolation_key"] == "abc_r2"
    assert d["duplicate_guard_bypassed_for_validation"] is False
