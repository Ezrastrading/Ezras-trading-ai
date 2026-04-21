"""Gate B configuration — env-backed dataclass for engine + staged proofs."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return float(default)
    return float(raw)


def _i(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return int(default)
    return int(raw)


def _b(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class GateBConfig:
    strategy_family: str = "gate_b_momentum_v1"
    scan_interval_sec: float = 60.0
    disable_gate_b_in_chop: bool = False
    max_simultaneous_positions: int = 4
    max_quote_age_sec: float = 8.0
    min_volume_24h_usd: float = 2_000_000.0
    max_spread_bps: float = 50.0
    min_book_depth_usd: float = 25_000.0
    min_breakout_move_pct: float = 0.05
    min_volume_surge_ratio: float = 1.5
    min_continuation_candles: int = 2
    min_momentum_score_entry: float = 0.35
    max_high_corr_positions: int = 3
    reentry_cooldown_sec: float = 300.0
    momentum_threshold_0_100: float = 40.0
    momentum_top_k: int = 3
    min_liquidity_score: float = 0.45
    min_momentum_score: float = 0.35
    sudden_drop_exit_pct: float = 0.08
    sudden_spike_review_pct: float = 0.15
    profit_exit_slippage_buffer_pct: float = 0.002
    profit_zone_min_pct: float = 0.10
    profit_zone_max_pct: float = 0.11
    trailing_stop_from_peak_pct: float = 0.03
    hard_stop_from_entry_pct: float = 0.12
    max_hold_sec: float = 86_400.0


def load_gate_b_config_from_env() -> GateBConfig:
    return GateBConfig(
        strategy_family=os.environ.get("GATE_B_STRATEGY_FAMILY", GateBConfig.strategy_family),
        scan_interval_sec=_f("GATE_B_SCAN_INTERVAL_SEC", GateBConfig.scan_interval_sec),
        disable_gate_b_in_chop=_b("GATE_B_DISABLE_IN_CHOP", GateBConfig.disable_gate_b_in_chop),
        max_simultaneous_positions=_i("GATE_B_MAX_SIMULTANEOUS_POSITIONS", GateBConfig.max_simultaneous_positions),
        max_quote_age_sec=_f("GATE_B_MAX_QUOTE_AGE_SEC", GateBConfig.max_quote_age_sec),
        min_volume_24h_usd=_f("GATE_B_MIN_VOLUME_24H_USD", GateBConfig.min_volume_24h_usd),
        max_spread_bps=_f("GATE_B_MAX_SPREAD_BPS", GateBConfig.max_spread_bps),
        min_book_depth_usd=_f("GATE_B_MIN_BOOK_DEPTH_USD", GateBConfig.min_book_depth_usd),
        min_breakout_move_pct=_f("GATE_B_MIN_BREAKOUT_MOVE_PCT", GateBConfig.min_breakout_move_pct),
        min_volume_surge_ratio=_f("GATE_B_MIN_VOLUME_SURGE_RATIO", GateBConfig.min_volume_surge_ratio),
        min_continuation_candles=_i("GATE_B_MIN_CONTINUATION_CANDLES", GateBConfig.min_continuation_candles),
        min_momentum_score_entry=_f("GATE_B_MIN_MOMENTUM_SCORE_ENTRY", GateBConfig.min_momentum_score_entry),
        max_high_corr_positions=_i("GATE_B_MAX_HIGH_CORR_POSITIONS", GateBConfig.max_high_corr_positions),
        reentry_cooldown_sec=_f("GATE_B_REENTRY_COOLDOWN_SEC", GateBConfig.reentry_cooldown_sec),
        momentum_threshold_0_100=_f("GATE_B_MOMENTUM_THRESHOLD", GateBConfig.momentum_threshold_0_100),
        momentum_top_k=_i("GATE_B_MOMENTUM_TOP_K", GateBConfig.momentum_top_k),
        min_liquidity_score=_f("GATE_B_MIN_LIQUIDITY_SCORE", GateBConfig.min_liquidity_score),
        min_momentum_score=_f("GATE_B_MIN_MOMENTUM_SCORE", GateBConfig.min_momentum_score),
        sudden_drop_exit_pct=_f("GATE_B_SUDDEN_DROP_EXIT_PCT", GateBConfig.sudden_drop_exit_pct),
        sudden_spike_review_pct=_f("GATE_B_SUDDEN_SPIKE_REVIEW_PCT", GateBConfig.sudden_spike_review_pct),
        profit_exit_slippage_buffer_pct=_f(
            "GATE_B_PROFIT_EXIT_SLIPPAGE_BUFFER_PCT", GateBConfig.profit_exit_slippage_buffer_pct
        ),
        profit_zone_min_pct=_f("GATE_B_PROFIT_ZONE_MIN_PCT", GateBConfig.profit_zone_min_pct),
        profit_zone_max_pct=_f("GATE_B_PROFIT_ZONE_MAX_PCT", GateBConfig.profit_zone_max_pct),
        trailing_stop_from_peak_pct=_f("GATE_B_TRAILING_STOP_FROM_PEAK_PCT", GateBConfig.trailing_stop_from_peak_pct),
        hard_stop_from_entry_pct=_f("GATE_B_HARD_STOP_FROM_ENTRY_PCT", GateBConfig.hard_stop_from_entry_pct),
        max_hold_sec=_f("GATE_B_MAX_HOLD_SEC", GateBConfig.max_hold_sec),
    )
