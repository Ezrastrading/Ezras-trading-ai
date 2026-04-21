"""Adaptive operating system — emergency brake, recovery, scaling, market gates."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from trading_ai.control.adaptive_operating_system import (
    PersistedOperatingState,
    evaluate_adaptive_operating_system,
    load_persisted_state,
    save_persisted_state,
    write_operator_operating_mode_txt,
)
from trading_ai.control.emergency_brake import evaluate_emergency_brake
from trading_ai.control.market_awareness import evaluate_market_quality_for_scaling
from trading_ai.control.operating_mode_types import OperatingMode, OperatingModeConfig, OperatingSnapshot


@pytest.fixture
def rt(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_halt_on_consecutive_losses(rt: Path, monkeypatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(rt))
    snap = OperatingSnapshot(
        consecutive_losses=6,
        last_n_trade_pnls=[-1, -1, -1, -1, -1, -1],
        rolling_equity_high=100_000,
        current_equity=99_000,
    )
    cfg = OperatingModeConfig(max_consecutive_losses=5)
    out = evaluate_adaptive_operating_system(snap, cfg=cfg, persisted=PersistedOperatingState(mode="normal"))
    assert out.mode == OperatingMode.HALTED
    assert out.emergency_brake_triggered


def test_halt_on_loss_rate(rt: Path) -> None:
    pnls = [-1.0] * 14 + [0.5]
    snap = OperatingSnapshot(consecutive_losses=0, last_n_trade_pnls=pnls, rolling_equity_high=100_000, current_equity=100_000)
    cfg = OperatingModeConfig(loss_rate_window_n=15, max_loss_rate_last_n_trades=0.65)
    b = evaluate_emergency_brake(snap, cfg)
    assert b.triggered
    assert b.recommended_floor == OperatingMode.HALTED


def test_halt_on_drawdown(rt: Path) -> None:
    snap = OperatingSnapshot(
        consecutive_losses=0,
        last_n_trade_pnls=[1.0] * 10,
        rolling_equity_high=100_000,
        current_equity=85_000,
    )
    cfg = OperatingModeConfig(max_rolling_drawdown_pct=0.12)
    b = evaluate_emergency_brake(snap, cfg)
    assert b.triggered


def test_negative_expectancy_defensive_not_always_halt(rt: Path) -> None:
    snap = OperatingSnapshot(
        consecutive_losses=0,
        last_n_trade_pnls=[-2.0, -2.0, -2.0, -2.0, 1.0, 1.0, 1.0, 1.0],
        rolling_equity_high=100_000,
        current_equity=100_000,
    )
    cfg = OperatingModeConfig()
    b = evaluate_emergency_brake(snap, cfg)
    assert b.recommended_floor == OperatingMode.DEFENSIVE


def test_infrastructure_halt(rt: Path) -> None:
    snap = OperatingSnapshot(
        reconciliation_failures_24h=3,
        last_n_trade_pnls=[1.0] * 5,
        rolling_equity_high=100_000,
        current_equity=100_000,
    )
    b = evaluate_emergency_brake(snap, OperatingModeConfig())
    assert b.recommended_floor == OperatingMode.HALTED


def test_recovery_halted_to_defensive_after_cooldown(rt: Path, monkeypatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(rt))
    import time as _t

    snap = OperatingSnapshot(
        consecutive_losses=0,
        last_n_trade_pnls=[0.5] * 40,
        rolling_equity_high=100_000,
        current_equity=100_000,
        slippage_health=0.9,
        liquidity_health=0.9,
        market_regime="trending",
        market_chop_score=0.2,
    )
    cfg = OperatingModeConfig(recovery_cooldown_sec_after_halt=0.0, min_sample_for_confident_mode=5)
    p = PersistedOperatingState(mode="halted", halt_entry_ts=_t.time() - 10_000)
    out = evaluate_adaptive_operating_system(snap, cfg=cfg, persisted=p)
    assert out.mode == OperatingMode.DEFENSIVE


def test_no_scale_when_liquidity_bad_despite_pnl(rt: Path) -> None:
    mq = evaluate_market_quality_for_scaling(
        liquidity_health=0.2,
        slippage_health=0.9,
        market_regime="neutral",
        market_chop_score=0.2,
    )
    assert mq["market_quality_allows_aggressive_scale"] is False


def test_not_martingale_size_mult(rt: Path) -> None:
    from trading_ai.control.emergency_brake import mode_size_multiplier

    m, _ = mode_size_multiplier(OperatingMode.DEFENSIVE, OperatingModeConfig())
    assert m < 1.0


def test_persistence_roundtrip(rt: Path, monkeypatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(rt))
    st = PersistedOperatingState(mode="cautious", cycles_since_mode_change=5)
    save_persisted_state(st)
    loaded = load_persisted_state()
    assert loaded.mode == "cautious"


def test_operator_txt(rt: Path, monkeypatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(rt))
    p = write_operator_operating_mode_txt({"operating_mode": "halted", "prior_mode": "normal", "mode_change_reasons": ["x"], "allow_new_trades": False, "size_multiplier_effective": 0.0, "emergency_brake_triggered": True, "restart_ready": False, "confidence_scaling_ready": False})
    assert p.is_file()


def test_brake_overrides_scale_up(rt: Path, monkeypatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(rt))
    snap = OperatingSnapshot(
        consecutive_losses=6,
        last_n_trade_pnls=[1.0] * 50,
        rolling_equity_high=100_000,
        current_equity=100_000,
        slippage_health=0.95,
        liquidity_health=0.95,
    )
    cfg = OperatingModeConfig(max_consecutive_losses=5)
    out = evaluate_adaptive_operating_system(snap, cfg=cfg, persisted=PersistedOperatingState(mode="aggressive_confirmed"))
    assert out.mode == OperatingMode.HALTED
