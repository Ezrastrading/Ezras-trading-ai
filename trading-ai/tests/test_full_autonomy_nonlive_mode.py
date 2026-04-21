from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_full_autonomy_nonlive_writes_artifacts_and_disables_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.control.full_autonomy_mode import write_full_autonomy_mode_artifacts

    out = write_full_autonomy_mode_artifacts(runtime_root=tmp_path, reason="test")
    mode = out["mode"]
    status = out["status"]

    assert mode["mode"] == "FULL_AUTONOMY_NONLIVE"
    assert status["live_trading_disabled"] is True
    assert status["live_orders_allowed"] is False
    assert (tmp_path / "data" / "control" / "full_autonomy_mode.json").is_file()
    assert (tmp_path / "data" / "control" / "full_autonomy_live_status.json").is_file()

    # Fail-closed flags are applied.
    assert os.environ.get("NTE_EXECUTION_MODE") == "paper"
    assert (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").lower() in ("false", "0", "no", "")
    assert (os.environ.get("COINBASE_ENABLED") or "").lower() in ("false", "0", "no", "")


def test_live_order_guard_blocks_in_full_autonomy_nonlive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.control.full_autonomy_mode import write_full_autonomy_mode_artifacts

    write_full_autonomy_mode_artifacts(runtime_root=tmp_path, reason="test_guard")

    from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted

    with pytest.raises(RuntimeError):
        assert_live_order_permitted(
            "place_market_entry",
            "coinbase",
            "BTC-USD",
            source="pytest",
            quote_notional=10.0,
            order_side="BUY",
        )


def test_sim_24h_writes_required_control_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.simulation.sim_24h import Sim24hConfig, run_simulated_24h_day

    out = run_simulated_24h_day(
        config=Sim24hConfig(hours=2, trades_per_hour=2, seed=1, runtime_root=tmp_path, accelerate_sleep_ms=0)
    )
    assert out["summary"]["live_trading_disabled"] is True
    for name in (
        "full_autonomy_mode.json",
        "full_autonomy_live_status.json",
        "sim_24h_summary.json",
        "sim_24h_timeline.json",
        "sim_24h_trade_log.json",
        "sim_24h_pnl.json",
        "sim_24h_lessons.json",
        "sim_24h_reviews.json",
        "sim_24h_comparisons.json",
        "sim_24h_tasks.json",
        "sim_24h_ceo.json",
        "sim_24h_final_verdict.json",
    ):
        assert (tmp_path / "data" / "control" / name).is_file()

