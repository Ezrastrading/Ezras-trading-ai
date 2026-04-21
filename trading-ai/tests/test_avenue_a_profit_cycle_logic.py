from __future__ import annotations

import time

from trading_ai.orchestration.avenue_a_profit_cycle import (
    GateAProfitConfig,
    _compute_exit_reason_gate_a,
    _estimate_required_move_bps,
    profit_mode_enabled,
)


def test_profit_mode_enabled_autonomous_defaults_true() -> None:
    assert profit_mode_enabled(mode="autonomous_live") is True
    assert profit_mode_enabled(mode="autonomous_paper") is True


def test_gate_a_min_hold_blocks_instant_exit() -> None:
    now = time.time()
    cfg = GateAProfitConfig(
        min_hold_sec=60.0,
        max_hold_sec=600.0,
        take_profit_pct=0.0001,
        stop_loss_pct=0.0001,
        trailing_stop_from_peak_pct=0.0001,
    )
    # Even if price moved enough for TP/SL, before min_hold there should be no exit.
    r = _compute_exit_reason_gate_a(
        now_ts=now,
        entry_ts=now - 10.0,
        entry_price=100.0,
        last_price=120.0,
        peak_price=120.0,
        cfg=cfg,
    )
    assert r is None


def test_gate_a_max_hold_forces_exit() -> None:
    now = time.time()
    cfg = GateAProfitConfig(min_hold_sec=10.0, max_hold_sec=20.0)
    r = _compute_exit_reason_gate_a(
        now_ts=now,
        entry_ts=now - 25.0,
        entry_price=100.0,
        last_price=100.0,
        peak_price=100.0,
        cfg=cfg,
    )
    assert r == "max_hold_timeout"


def test_estimate_required_move_bps_flags_fee_missing() -> None:
    req, fee_missing = _estimate_required_move_bps(
        spread_bps=12.0, est_total_fee_bps=None, floor_bps=3.0
    )
    assert fee_missing is True
    assert req == 15.0

