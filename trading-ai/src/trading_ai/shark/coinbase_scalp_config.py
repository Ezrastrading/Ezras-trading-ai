"""
Configuration for the Coinbase Advanced Trade crypto scalp loop (BTC-USD, ETH-USD).

Dollar targets are absolute unrealized P&L on the open position (not percent).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet, Tuple


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return default


@dataclass(frozen=True)
class CoinbaseScalpConfig:
    """Defaults match the scalp spec; override via COINBASE_SCALP_* env vars."""

    products: Tuple[str, ...] = ("BTC-USD", "ETH-USD")

    profit_target_usd_min: float = 0.03
    profit_target_usd_max: float = 0.08
    profit_target_usd_default: float = 0.04

    stop_loss_usd_min: float = -0.05
    stop_loss_usd_max: float = -0.03
    stop_loss_usd_default: float = -0.04

    scanner_interval_seconds: float = 20.0
    position_check_interval_seconds: float = 5.0

    soft_timeout_seconds: float = 60.0
    hard_timeout_seconds: float = 120.0

    max_open_positions: int = 1
    daily_loss_limit_usd: float = 50.0
    max_consecutive_losses: int = 5

    order_usd: float = 10.0
    momentum_lookback_seconds: float = 60.0
    momentum_trigger_pct: float = 0.001
    max_spread_pct: float = 0.0008
    min_quote_24h_volume_usd: float = 1_000_000.0

    stagnant_pnl_usd_abs: float = 0.02
    price_cache_max_age_seconds: float = 3.0
    exit_poll_max_seconds: float = 300.0

    enable_market_websocket: bool = True
    ws_url: str = "wss://advanced-trade-ws.coinbase.com"
    ws_user_url: str = "wss://advanced-trade-ws-user.coinbase.com"

    @property
    def allowed_products(self) -> FrozenSet[str]:
        return frozenset(self.products)

    @classmethod
    def from_env(cls) -> "CoinbaseScalpConfig":
        return cls(
            products=_parse_products(
                os.environ.get("COINBASE_SCALP_PRODUCTS"),
                default=("BTC-USD", "ETH-USD"),
            ),
            profit_target_usd_min=_env_float("COINBASE_SCALP_TP_MIN", 0.03),
            profit_target_usd_max=_env_float("COINBASE_SCALP_TP_MAX", 0.08),
            profit_target_usd_default=_env_float("COINBASE_SCALP_TP_DEFAULT", 0.04),
            stop_loss_usd_min=_env_float("COINBASE_SCALP_SL_MIN", -0.05),
            stop_loss_usd_max=_env_float("COINBASE_SCALP_SL_MAX", -0.03),
            stop_loss_usd_default=_env_float("COINBASE_SCALP_SL_DEFAULT", -0.04),
            scanner_interval_seconds=_env_float("COINBASE_SCALP_SCAN_INTERVAL_SEC", 20.0),
            position_check_interval_seconds=_env_float(
                "COINBASE_SCALP_POSITION_INTERVAL_SEC", 5.0
            ),
            soft_timeout_seconds=_env_float("COINBASE_SCALP_SOFT_TIMEOUT_SEC", 60.0),
            hard_timeout_seconds=_env_float("COINBASE_SCALP_HARD_TIMEOUT_SEC", 120.0),
            max_open_positions=max(1, _env_int("COINBASE_SCALP_MAX_OPEN", 1)),
            daily_loss_limit_usd=_env_float("COINBASE_SCALP_DAILY_LOSS_LIMIT", 50.0),
            max_consecutive_losses=max(1, _env_int("COINBASE_SCALP_MAX_CONSEC_LOSSES", 5)),
            order_usd=max(1.0, _env_float("COINBASE_SCALP_ORDER_USD", 10.0)),
            momentum_lookback_seconds=_env_float("COINBASE_SCALP_MOMENTUM_LOOKBACK_SEC", 60.0),
            momentum_trigger_pct=_env_float("COINBASE_SCALP_MOMENTUM_TRIG", 0.001),
            max_spread_pct=_env_float("COINBASE_SCALP_MAX_SPREAD_PCT", 0.0008),
            min_quote_24h_volume_usd=_env_float("COINBASE_SCALP_MIN_VOL_24H", 1_000_000.0),
            stagnant_pnl_usd_abs=_env_float("COINBASE_SCALP_STAGNANT_PNL_USD", 0.02),
            price_cache_max_age_seconds=_env_float("COINBASE_SCALP_PRICE_CACHE_AGE", 3.0),
            exit_poll_max_seconds=_env_float("COINBASE_SCALP_EXIT_POLL_MAX_SEC", 300.0),
            enable_market_websocket=_env_bool("COINBASE_SCALP_WS", True),
            ws_url=(os.environ.get("COINBASE_SCALP_WS_URL") or "wss://advanced-trade-ws.coinbase.com").strip(),
            ws_user_url=(
                os.environ.get("COINBASE_SCALP_WS_USER_URL")
                or "wss://advanced-trade-ws-user.coinbase.com"
            ).strip(),
        )


def _parse_products(raw: str | None, default: Tuple[str, ...]) -> Tuple[str, ...]:
    if not raw or not str(raw).strip():
        return default
    parts = tuple(p.strip() for p in str(raw).split(",") if p.strip())
    allowed = {"BTC-USD", "ETH-USD"}
    filt = tuple(p for p in parts if p in allowed)
    return filt if filt else default


def coinbase_scalp_enabled() -> bool:
    """Gate the standalone scalp process / integration (default off)."""
    return _env_bool("COINBASE_SCALP_ENABLED", False)


def clamp_tp_sl(cfg: CoinbaseScalpConfig) -> Tuple[float, float]:
    """Return (take_profit_usd, stop_loss_usd) clamped to configured ranges."""
    tp = min(
        max(cfg.profit_target_usd_default, cfg.profit_target_usd_min),
        cfg.profit_target_usd_max,
    )
    sl = max(
        min(cfg.stop_loss_usd_default, cfg.stop_loss_usd_max),
        cfg.stop_loss_usd_min,
    )
    return tp, sl
