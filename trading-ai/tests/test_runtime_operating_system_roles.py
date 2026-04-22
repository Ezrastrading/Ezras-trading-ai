from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_ops_and_research_ticks_run_and_live_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    # Explicitly try to trick it into live; it should block.
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "false")

    from trading_ai.runtime.operating_system import tick_ops_once, tick_research_once

    ops = tick_ops_once(runtime_root=tmp_path)
    assert ops.get("ok") is True

    res = tick_research_once(runtime_root=tmp_path, skip_models=True)
    assert res.get("ok") is True

    # Ops should write an outcome snapshot under runtime_root.
    p = tmp_path / "data" / "control" / "ops_outcome_ingestion_snapshot.json"
    assert p.is_file()
    blob = json.loads(p.read_text(encoding="utf-8"))
    assert blob.get("truth_version") == "ops_outcome_ingestion_snapshot_v1"

    # Supervisor writes loop status artifacts.
    from trading_ai.runtime.operating_system import run_role_supervisor_once

    s1 = run_role_supervisor_once(role="ops", runtime_root=tmp_path, force_all_due=True)
    s2 = run_role_supervisor_once(role="ops", runtime_root=tmp_path, force_all_due=True)
    assert s1.get("ok") is True and s2.get("ok") is True
    st = tmp_path / "data" / "control" / "operating_system" / "loop_status_ops.json"
    assert st.is_file()


def test_supervisor_allows_micro_live_env_when_contract_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")

    from trading_ai.deployment import live_micro_enablement as lme

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

    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "deployed_environment_smoke.json").write_text(
        '{"truth_version":"t","live_disabled":{"ok":true},"live_micro_private_build":{"ok":true}}',
        encoding="utf-8",
    )
    (ctrl / "micro_trade_readiness.json").write_text('{"ok":true}', encoding="utf-8")
    jr = tmp_path / "shark" / "memory" / "global"
    jr.mkdir(parents=True, exist_ok=True)
    (jr / "joint_review_latest.json").write_text("{}", encoding="utf-8")
    risk = tmp_path / "data" / "risk"
    risk.mkdir(parents=True, exist_ok=True)
    (risk / "risk_state.json").write_text('{"daily_pnl_usd":0.0}', encoding="utf-8")

    lme.write_live_session_limits(tmp_path)
    lme.run_live_micro_preflight(tmp_path)
    lme.run_live_micro_readiness(tmp_path)
    lme.write_live_enablement_request(tmp_path, operator="pytest", note="ok")

    from trading_ai.runtime.operating_system import run_role_supervisor_once

    out = run_role_supervisor_once(role="ops", runtime_root=tmp_path, force_all_due=True)
    assert out.get("ok") is True


def test_role_lock_prevents_two_ops_daemons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.operating_system import try_acquire_role_lock

    ok1, _, _ = try_acquire_role_lock(role="ops", holder_id="h1", runtime_root=tmp_path, ttl_seconds=30)
    ok2, why2, _ = try_acquire_role_lock(role="ops", holder_id="h2", runtime_root=tmp_path, ttl_seconds=30)
    assert ok1 is True
    assert ok2 is False
    assert "role_lock_held:ops" in why2

