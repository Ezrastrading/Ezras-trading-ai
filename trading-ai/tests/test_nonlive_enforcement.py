"""Hard block: simulation must not run under live-trading env flags."""

from __future__ import annotations

import os

import pytest

from pathlib import Path

from trading_ai.simulation.nonlive import LiveTradingNotAllowedError, assert_nonlive_for_simulation, nonlive_env_ok


def test_nonlive_env_ok_detects_live_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "false")
    monkeypatch.delenv("EZRA_LIVE_MICRO_ENABLED", raising=False)
    ok, why = nonlive_env_ok(runtime_root=tmp_path)
    assert ok is False
    assert why == "live_execution_env_detected"


def test_assert_nonlive_raises_on_coinbase_execution(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    with pytest.raises(LiveTradingNotAllowedError):
        assert_nonlive_for_simulation(runtime_root=tmp_path)


def test_nonlive_env_ok_allows_live_env_when_micro_live_contract_green(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")

    from trading_ai.deployment import live_micro_enablement as lme

    # Minimal artifact chain for contract ok.
    monkeypatch.setenv("EZRA_LIVE_MICRO_OPERATOR_CONFIRM", lme.OPERATOR_CONFIRM_VALUE)
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_NOTIONAL_USD", "5")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_DAILY_LOSS_USD", "25")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_TOTAL_EXPOSURE_USD", "15")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_PRODUCTS", "BTC-USD")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_AVENUE", "COINBASE")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_GATE", "gate_a")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION", "3")
    monkeypatch.setenv("EZRA_LIVE_MICRO_COOLDOWN_SEC", "0")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_CONCURRENT_POSITIONS", "1")

    monkeypatch.setattr("trading_ai.deployment.operator_env_contracts.missing_coinbase_credential_env_vars", lambda: [])
    monkeypatch.setattr("trading_ai.control.kill_switch.kill_switch_active", lambda: False)

    root = tmp_path
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "deployed_environment_smoke.json").write_text(
        '{"truth_version":"t","live_disabled":{"ok":true},"live_micro_private_build":{"ok":true}}',
        encoding="utf-8",
    )
    (ctrl / "micro_trade_readiness.json").write_text('{"ok":true}', encoding="utf-8")
    jr = root / "shark" / "memory" / "global"
    jr.mkdir(parents=True, exist_ok=True)
    (jr / "joint_review_latest.json").write_text("{}", encoding="utf-8")
    risk = root / "data" / "risk"
    risk.mkdir(parents=True, exist_ok=True)
    (risk / "risk_state.json").write_text('{"daily_pnl_usd":0.0}', encoding="utf-8")

    lme.write_live_session_limits(root)
    lme.run_live_micro_preflight(root)
    lme.run_live_micro_readiness(root)
    lme.write_live_enablement_request(root, operator="pytest", note="ok")

    ok, why = nonlive_env_ok(runtime_root=root)
    assert ok is True
    assert why == "live_micro_contract_authorizes_live_env"
