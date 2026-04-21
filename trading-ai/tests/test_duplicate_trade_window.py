"""Canonical duplicate-window parsing and failsafe duplicate behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.safety.duplicate_trade_window import (
    effective_duplicate_window_seconds,
    parse_duplicate_trade_window_from_env,
    persisted_seconds_for_duplicate_check,
)
from trading_ai.safety.failsafe_guard import FailsafeContext, run_failsafe_checks


def test_parse_unset() -> None:
    r = parse_duplicate_trade_window_from_env(environ={})
    assert r.kind == "unset"
    assert effective_duplicate_window_seconds(r) == 45.0


def test_parse_explicit_zero() -> None:
    r = parse_duplicate_trade_window_from_env(environ={"EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC": "0"})
    assert r.kind == "seconds"
    assert r.window_seconds == 0.0
    assert effective_duplicate_window_seconds(r) == 0.0


def test_parse_string_zero() -> None:
    r = parse_duplicate_trade_window_from_env(environ={"EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC": "0.0"})
    assert r.kind == "seconds"
    assert effective_duplicate_window_seconds(r) == 0.0


def test_parse_disabled_flag() -> None:
    r = parse_duplicate_trade_window_from_env(
        environ={
            "EZRAS_FAILSAFE_DUPLICATE_WINDOW_DISABLED": "1",
            "EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC": "45",
        }
    )
    assert r.kind == "disabled"
    assert effective_duplicate_window_seconds(r) is None


def test_parse_positive() -> None:
    r = parse_duplicate_trade_window_from_env(environ={"EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC": "120"})
    assert r.kind == "seconds"
    assert effective_duplicate_window_seconds(r) == 120.0


def test_parse_bad_env_falls_back_unset() -> None:
    r = parse_duplicate_trade_window_from_env(environ={"EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC": "not_a_float"})
    assert r.kind == "unset"
    assert r.parse_note is not None


def test_persisted_explicit_zero_not_coerced_to_default() -> None:
    win, res = persisted_seconds_for_duplicate_check(
        {"duplicate_window_sec": 0.0},
        environ={},
    )
    assert res.kind == "unset"
    assert win == 0.0


@pytest.fixture
def rt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_execution_mirror_stable_with_skip_duplicate(rt: Path) -> None:
    from trading_ai.control.system_execution_lock import ensure_system_execution_lock_file
    from trading_ai.prelive.execution_mirror import run as mirror_run

    ensure_system_execution_lock_file(runtime_root=rt)
    out = mirror_run(runtime_root=rt)
    assert out.get("ok") is True


def test_duplicate_guard_blocks_within_window(rt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.control.system_execution_lock import ensure_system_execution_lock_file

    monkeypatch.setenv("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC", "3600")
    ensure_system_execution_lock_file(runtime_root=rt)

    ctx = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_a",
        quote_notional=50.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e6},
        strategy_id="t",
        trade_id="t_dup_a",
        multi_leg=False,
        skip_governance=True,
        skip_duplicate_guard=False,
    )
    ok1, _, _ = run_failsafe_checks(ctx, runtime_root=rt)
    assert ok1 is True
    ctx2 = FailsafeContext(
        action="place_market_entry",
        avenue_id="coinbase",
        product_id="BTC-USD",
        gate="gate_a",
        quote_notional=51.0,
        base_size=None,
        quote_balances_by_ccy={"USD": 1e6},
        strategy_id="t",
        trade_id="t_dup_b",
        multi_leg=False,
        skip_governance=True,
        skip_duplicate_guard=False,
    )
    ok2, code2, _ = run_failsafe_checks(ctx2, runtime_root=rt)
    assert ok2 is False
    assert "duplicate" in code2.lower()
