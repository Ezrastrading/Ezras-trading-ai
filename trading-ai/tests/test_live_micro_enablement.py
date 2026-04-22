"""Unit tests for controlled micro-live enablement (fail-closed; no venue orders)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture
def micro_preflight_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trading_ai.deployment.operator_env_contracts.missing_coinbase_credential_env_vars",
        lambda: [],
    )
    monkeypatch.setattr("trading_ai.control.kill_switch.kill_switch_active", lambda: False)


def _seed_micro_env(monkeypatch: pytest.MonkeyPatch, *, products: str = "BTC-USD") -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_OPERATOR_CONFIRM", "I_ACCEPT_MICRO_LIVE_CAPITAL_RISK_AND_LIMITS")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_NOTIONAL_USD", "5")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_DAILY_LOSS_USD", "25")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_TOTAL_EXPOSURE_USD", "15")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_PRODUCTS", products)
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_AVENUE", "COINBASE")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_GATE", "gate_a")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION", "3")
    monkeypatch.setenv("EZRA_LIVE_MICRO_COOLDOWN_SEC", "0")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_CONCURRENT_POSITIONS", "1")


def _write_minimal_preflight_ok(root: Path) -> None:
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    now = time.time()
    (ctrl / "deployed_environment_smoke.json").write_text(
        json.dumps(
            {
                "truth_version": "t",
                "live_disabled": {"ok": True},
                "live_micro_private_build": {"ok": True},
            }
        ),
        encoding="utf-8",
    )
    (ctrl / "micro_trade_readiness.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    for p in (ctrl / "deployed_environment_smoke.json", ctrl / "micro_trade_readiness.json"):
        p.touch(exist_ok=True)

    jr = root / "shark" / "memory" / "global"
    jr.mkdir(parents=True, exist_ok=True)
    (jr / "joint_review_latest.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    risk = root / "data" / "risk"
    risk.mkdir(parents=True, exist_ok=True)
    (risk / "risk_state.json").write_text(json.dumps({"daily_pnl_usd": 0.0}), encoding="utf-8")


def test_contract_ok_when_micro_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EZRA_LIVE_MICRO_ENABLED", raising=False)
    from trading_ai.deployment.live_micro_enablement import assert_live_micro_runtime_contract

    ok, err, _ = assert_live_micro_runtime_contract(tmp_path, phase="test")
    assert ok is True
    assert err == ""


def test_contract_fails_when_micro_enabled_missing_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")
    from trading_ai.deployment.live_micro_enablement import assert_live_micro_runtime_contract

    ok, err, _ = assert_live_micro_runtime_contract(tmp_path, phase="test")
    assert ok is False
    assert "live_session_limits" in err or "live_preflight" in err


def test_force_halt_blocks_before_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, micro_preflight_patches: None
) -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")
    _seed_micro_env(monkeypatch)
    from trading_ai.deployment import live_micro_enablement as lme

    root = tmp_path
    _write_minimal_preflight_ok(root)
    lme.write_live_session_limits(root)
    lme.run_live_micro_preflight(root)
    lme.run_live_micro_readiness(root)
    lme.write_live_enablement_request(root, operator="pytest", note="halt")
    lme.write_live_micro_force_halt(root, operator="pytest", reason="t")

    ok, err, _ = lme.assert_live_micro_runtime_contract(root, phase="test")
    assert ok is False
    assert "force_halt" in err or "pause" in err


def test_enforce_blocks_wrong_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, micro_preflight_patches: None
) -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")
    _seed_micro_env(monkeypatch, products="BTC-USD")
    from trading_ai.deployment import live_micro_enablement as lme

    root = tmp_path
    _write_minimal_preflight_ok(root)
    lme.write_live_session_limits(root)
    lme.run_live_micro_preflight(root)
    lme.run_live_micro_readiness(root)
    lme.write_live_enablement_request(root, operator="pytest", note="x")

    with pytest.raises(RuntimeError, match="product_not_allowed"):
        lme.enforce_live_micro_order_guards(
            runtime_root=root,
            avenue_id="coinbase",
            product_id="ETH-USD",
            execution_gate="gate_a",
            quote_notional=2.0,
            action="place_market_entry",
            order_side="BUY",
        )


def test_enforce_allows_sell_when_at_position_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, micro_preflight_patches: None
) -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")
    _seed_micro_env(monkeypatch)
    from trading_ai.deployment import live_micro_enablement as lme

    root = tmp_path
    _write_minimal_preflight_ok(root)
    lme.write_live_session_limits(root)
    lme.run_live_micro_preflight(root)
    lme.run_live_micro_readiness(root)
    lme.write_live_enablement_request(root, operator="pytest", note="x")

    st = root / "data" / "control" / "live_micro_session_state.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text(
        json.dumps({"open_live_positions": 1, "session_notional_usd": 5.0, "session_trades_completed": 0}),
        encoding="utf-8",
    )

    lme.enforce_live_micro_order_guards(
        runtime_root=root,
        avenue_id="coinbase",
        product_id="BTC-USD",
        execution_gate="gate_a",
        quote_notional=None,
        action="place_market_exit",
        order_side="SELL",
    )


def test_session_open_and_close_bookkeeping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, micro_preflight_patches: None
) -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")
    _seed_micro_env(monkeypatch)
    from trading_ai.deployment import live_micro_enablement as lme

    root = tmp_path
    _write_minimal_preflight_ok(root)
    lme.write_live_session_limits(root)
    lme.run_live_micro_preflight(root)
    lme.run_live_micro_readiness(root)
    lme.write_live_enablement_request(root, operator="pytest", note="x")

    lme.touch_live_micro_session_open_increment(root, quote_usd=4.0)
    st = json.loads((root / "data" / "control" / "live_micro_session_state.json").read_text(encoding="utf-8"))
    assert st["open_live_positions"] == 1
    assert abs(float(st["session_notional_usd"]) - 4.0) < 1e-9

    lme.touch_live_micro_session_trade_completed(root, quote_usd=4.0)
    st2 = json.loads((root / "data" / "control" / "live_micro_session_state.json").read_text(encoding="utf-8"))
    assert st2["open_live_positions"] == 0
    assert float(st2["session_notional_usd"]) == 0.0
    assert st2["session_trades_completed"] == 1


def test_session_limits_all_expands_gate_and_preserves_products_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, micro_preflight_patches: None
) -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")
    _seed_micro_env(monkeypatch)
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_GATE", "all")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_PRODUCTS", "all")
    from trading_ai.deployment import live_micro_enablement as lme

    lim = lme.write_live_session_limits(tmp_path)
    assert lim.get("allowed_gate_raw") == "all"
    assert set(lim.get("allowed_gates") or []) == {"gate_a", "gate_b"}
    assert lim.get("allowed_products_raw") == "all"
    assert lim.get("allow_all_products") is True
    assert lim.get("allowed_products") == []


def test_preflight_fails_stale_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, micro_preflight_patches: None
) -> None:
    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "false")
    _seed_micro_env(monkeypatch)
    from trading_ai.deployment import live_micro_enablement as lme

    root = tmp_path
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    p = ctrl / "deployed_environment_smoke.json"
    p.write_text(json.dumps({"truth_version": "t", "live_disabled": {"ok": True}}), encoding="utf-8")
    old = time.time() - 400_000
    import os

    os.utime(p, (old, old))
    (ctrl / "micro_trade_readiness.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    jr = root / "shark" / "memory" / "global"
    jr.mkdir(parents=True, exist_ok=True)
    (jr / "joint_review_latest.json").write_text("{}", encoding="utf-8")

    out = lme.run_live_micro_preflight(root, max_artifact_age_sec=86_400.0)
    assert out.get("ok") is False
    assert any("stale" in b for b in (out.get("blockers") or []))
