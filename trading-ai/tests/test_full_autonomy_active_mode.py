"""FULL_AUTONOMY_ACTIVE persistence and guard interaction."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_full_autonomy_active_writes_artifacts_without_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.control.full_autonomy_mode import resolve_full_autonomy_mode, write_full_autonomy_active_live_artifacts

    out = write_full_autonomy_active_live_artifacts(runtime_root=tmp_path, reason="test", apply_env=False)
    mode = out["mode"]
    status = out["status"]

    assert mode["mode"] == "FULL_AUTONOMY_ACTIVE"
    assert mode.get("LIVE_TRADING_ENABLED") is True
    assert status.get("LIVE_TRADING_ENABLED") is True
    assert status["live_orders_allowed"] is True
    assert (tmp_path / "data" / "control" / "full_autonomy_mode.json").is_file()
    assert (tmp_path / "data" / "control" / "full_autonomy_live_status.json").is_file()

    st = resolve_full_autonomy_mode(runtime_root=tmp_path)
    assert st.mode == "FULL_AUTONOMY_ACTIVE"
    assert st.live_trading_disabled is False


def test_live_order_guard_blocks_without_credentials_under_live_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    from trading_ai.control.full_autonomy_mode import write_full_autonomy_active_live_artifacts
    from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted

    write_full_autonomy_active_live_artifacts(runtime_root=tmp_path, reason="test_guard", apply_env=False)

    with pytest.raises(RuntimeError):
        assert_live_order_permitted(
            "place_market_entry",
            "coinbase",
            "BTC-USD",
            source="pytest",
            quote_notional=10.0,
            order_side="BUY",
        )


def test_nonlive_simulation_allowed_with_active_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    from trading_ai.control.full_autonomy_mode import write_full_autonomy_active_live_artifacts
    from trading_ai.simulation.nonlive import assert_nonlive_for_simulation

    write_full_autonomy_active_live_artifacts(runtime_root=tmp_path, reason="test_sim", apply_env=False)
    assert_nonlive_for_simulation(runtime_root=tmp_path)
