"""
Coinbase Avenue 1 — initial live parameters (maker-first entries, A/B router, C sandbox).

Defaults match the launch posture: BTC/ETH only, A+B live weighted, C sandbox,
strict filters, limit-first entries, market exits only when required.

Override via env ``NTE_AVE1_*`` or ``JSON`` path ``NTE_AVE1_CONFIG_PATH`` (future).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class GlobalLaunchRisk:
    products: Tuple[str, ...] = ("BTC-USD", "ETH-USD")
    max_open_positions: int = 2
    max_pending_orders_total: int = 2
    # First live session only — extra safety (env NTE_LAUNCH_CLAMP=1)
    launch_session_clamp: bool = False
    clamp_max_open_positions: int = 1
    clamp_equity_per_trade_pct_max: float = 0.10
    clamp_equity_per_trade_pct_min: float = 0.05
    clamp_pause_entries_after_consecutive_losses: int = 2
    daily_loss_limit_pct: float = 0.04
    soft_daily_loss_warning_pct: float = 0.025
    pause_after_consecutive_losses: int = 4
    pause_after_3_losses_same_strategy: bool = True
    max_new_entries_if_degraded: int = 0
    allow_exit_only_if_degraded: bool = True
    degraded_mode_blocks_entries: bool = True
    shadow_compare_enabled: bool = True
    research_enabled: bool = True
    briefing_runs_per_day: int = 2
    briefing_mode_low_token: bool = True
    compare_a_vs_b_in_ceo: bool = True


@dataclass(frozen=True)
class StrategyMeanReversion:
    strategy_id: str = "mean_reversion"
    enabled_live: bool = True
    priority_weight: float = 1.0
    entry_order_type: str = "post_only_limit"
    entry_offset_bps_btc: float = 4.0
    entry_offset_bps_eth: float = 5.0
    max_spread_bps_btc: float = 6.0
    max_spread_bps_eth: float = 8.0
    max_short_vol_bps_btc: float = 18.0
    max_short_vol_bps_eth: float = 22.0
    zscore_entry_threshold: float = 1.35
    min_net_edge_bps_after_cost: float = 18.0
    stale_pending_seconds: float = 45.0
    max_pending_entries_per_asset: int = 1
    time_stop_seconds: int = 180
    take_profit_pct: float = 0.0055
    stop_loss_pct: float = 0.0045
    trailing_arm_pct: float = 0.005
    trailing_lock_pct: float = 0.0025
    base_position_pct_equity: float = 0.15
    max_position_pct_equity: float = 0.20


@dataclass(frozen=True)
class StrategyContinuationPullback:
    strategy_id: str = "continuation_pullback"
    enabled_live: bool = True
    priority_weight: float = 0.80
    entry_order_type: str = "post_only_limit_preferred"
    entry_offset_bps_btc: float = 2.0
    entry_offset_bps_eth: float = 3.0
    max_spread_bps_btc: float = 5.0
    max_spread_bps_eth: float = 7.0
    max_short_vol_bps_btc: float = 20.0
    max_short_vol_bps_eth: float = 24.0
    pullback_depth_threshold_pct: float = 0.0018
    trend_strength_threshold: float = 0.65
    continuation_score_min: float = 0.72
    min_net_edge_bps_after_cost: float = 20.0
    stale_pending_seconds: float = 35.0
    time_stop_seconds: int = 240
    take_profit_pct: float = 0.0065
    stop_loss_pct: float = 0.0048
    trailing_arm_pct: float = 0.0055
    trailing_lock_pct: float = 0.0025
    base_position_pct_equity: float = 0.12
    max_position_pct_equity: float = 0.18


@dataclass(frozen=True)
class StrategyMicroMomentum:
    strategy_id: str = "micro_momentum"
    enabled_live: bool = False
    sandbox_only: bool = True
    candidate_score_min: float = 0.78
    max_spread_bps: float = 4.0
    max_short_vol_bps: float = 16.0
    record_candidate_signals: bool = True


@dataclass(frozen=True)
class RouterParams:
    prefer_a_if_score_difference_under: float = 0.05
    no_trade_if_both_below_threshold: bool = True
    require_post_fee_positive_expectancy: bool = True
    a_weight: float = 1.0
    b_weight: float = 0.8
    c_weight_live: float = 0.0


@dataclass(frozen=True)
class FeeAssumptions:
    # Conservative defaults — replace with Advanced Trade transaction summary / fee tier when live.
    estimated_maker_fee_pct: float = 0.0015
    estimated_taker_fee_pct: float = 0.0025
    required_edge_multiple_of_estimated_cost: float = 1.7


@dataclass(frozen=True)
class UserStreamParams:
    user_stream_stale_seconds: float = 15.0
    market_data_stale_seconds: float = 5.0
    polling_fallback_enabled: bool = True
    polling_fallback_interval_seconds: float = 5.0


@dataclass(frozen=True)
class CEOReviewParams:
    midday_review_time: str = "12:00"
    endday_review_time: str = "17:00"
    timezone: str = "America/New_York"
    low_token_mode: bool = True


@dataclass(frozen=True)
class CoinbaseAvenue1Launch:
    global_risk: GlobalLaunchRisk = field(default_factory=GlobalLaunchRisk)
    strategy_a: StrategyMeanReversion = field(default_factory=StrategyMeanReversion)
    strategy_b: StrategyContinuationPullback = field(default_factory=StrategyContinuationPullback)
    strategy_c: StrategyMicroMomentum = field(default_factory=StrategyMicroMomentum)
    router: RouterParams = field(default_factory=RouterParams)
    fees: FeeAssumptions = field(default_factory=FeeAssumptions)
    user_stream: UserStreamParams = field(default_factory=UserStreamParams)
    ceo: CEOReviewParams = field(default_factory=CEOReviewParams)


def load_coinbase_avenue1_launch() -> CoinbaseAvenue1Launch:
    """Load launch profile; env overrides for a subset (extend as needed)."""
    clamp = os.environ.get("NTE_LAUNCH_CLAMP", "").strip().lower() in ("1", "true", "yes")
    g = GlobalLaunchRisk(
        max_open_positions=int(os.environ.get("NTE_AVE1_MAX_OPEN", "2")),
        daily_loss_limit_pct=float(os.environ.get("NTE_AVE1_DAILY_LOSS_PCT", "0.04")),
        shadow_compare_enabled=os.environ.get("NTE_AVE1_SHADOW", "true").lower()
        in ("1", "true", "yes"),
        launch_session_clamp=clamp,
        clamp_max_open_positions=int(os.environ.get("NTE_LAUNCH_CLAMP_MAX_POS", "1")),
        clamp_equity_per_trade_pct_max=float(os.environ.get("NTE_LAUNCH_CLAMP_PCT_MAX", "0.10")),
        clamp_equity_per_trade_pct_min=float(os.environ.get("NTE_LAUNCH_CLAMP_PCT_MIN", "0.05")),
        clamp_pause_entries_after_consecutive_losses=int(
            os.environ.get("NTE_LAUNCH_CLAMP_LOSS_PAUSE", "2")
        ),
    )
    fees = FeeAssumptions(
        estimated_maker_fee_pct=float(os.environ.get("NTE_FEE_MAKER_PCT", "0.0015")),
        estimated_taker_fee_pct=float(os.environ.get("NTE_FEE_TAKER_PCT", "0.0025")),
    )
    return CoinbaseAvenue1Launch(global_risk=g, fees=fees)


def spread_cap_pct(product_id: str, launch: CoinbaseAvenue1Launch) -> float:
    """Max spread as fraction (e.g. 0.0006 = 6 bps) — use stricter of A/B for filter."""
    if "BTC" in product_id.upper():
        bps = min(launch.strategy_a.max_spread_bps_btc, launch.strategy_b.max_spread_bps_btc)
    else:
        bps = min(launch.strategy_a.max_spread_bps_eth, launch.strategy_b.max_spread_bps_eth)
    return bps / 10000.0


def vol_cap_bps(product_id: str, launch: CoinbaseAvenue1Launch) -> float:
    if "BTC" in product_id.upper():
        return max(launch.strategy_a.max_short_vol_bps_btc, launch.strategy_b.max_short_vol_bps_btc)
    return max(launch.strategy_a.max_short_vol_bps_eth, launch.strategy_b.max_short_vol_bps_eth)


def entry_offset_bps(product_id: str, strategy: str, launch: CoinbaseAvenue1Launch) -> float:
    btc = "BTC" in product_id.upper()
    if strategy == "mean_reversion":
        return launch.strategy_a.entry_offset_bps_btc if btc else launch.strategy_a.entry_offset_bps_eth
    if strategy == "continuation_pullback":
        return (
            launch.strategy_b.entry_offset_bps_btc if btc else launch.strategy_b.entry_offset_bps_eth
        )
    return 5.0


def launch_config_as_dict(launch: CoinbaseAvenue1Launch) -> Dict[str, Any]:
    """For logging / CEO / pre-live verification."""
    return {
        "global": launch.global_risk.__dict__,
        "strategy_a": launch.strategy_a.__dict__,
        "strategy_b": launch.strategy_b.__dict__,
        "strategy_c": launch.strategy_c.__dict__,
        "router": launch.router.__dict__,
        "fees": launch.fees.__dict__,
        "user_stream": launch.user_stream.__dict__,
        "ceo": launch.ceo.__dict__,
    }
