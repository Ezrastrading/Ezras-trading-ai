"""
Configuration for the Kalshi short-hold scalp engine (demo / paper-first).

Environment overrides use the ``KALSHI_SCALP_*`` prefix where noted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet, Optional, Tuple


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _parse_daily_loss_limit() -> Optional[float]:
    raw = (os.environ.get("KALSHI_SCALP_DAILY_LOSS_LIMIT_USD") or "").strip().lower()
    if raw in ("none", "unlimited", "off"):
        return None
    if not raw:
        return 50.0
    try:
        return float(raw)
    except ValueError:
        return 50.0


@dataclass
class KalshiScalpConfig:
    """Defaults match the scalp engine specification; all caps are aspirations, not guarantees."""

    allowed_market_families: Tuple[str, ...] = ("S&P", "BTC", "ETH")

    deployment_per_trade_usd: float = 2.0
    default_profit_target_dollars: float = 0.04
    profit_target_range_dollars: Tuple[float, float] = (0.03, 0.08)

    default_stop_loss_dollars: float = -0.04
    stop_loss_range_dollars: Tuple[float, float] = (-0.05, -0.03)

    scanner_interval_seconds: float = 20.0
    position_check_interval_seconds: float = 5.0

    soft_timeout_seconds: float = 60.0
    hard_timeout_seconds: float = 120.0

    max_open_positions: int = 1
    max_trade_attempts_per_hour: int = 12
    max_completed_trades_per_hour: int = 8

    daily_loss_limit_usd: Optional[float] = 50.0
    max_consecutive_losses: int = 5

    min_volume_fp: float = 1.0
    max_spread_prob: float = 0.06
    min_top_of_book_contracts: float = 5.0
    emergency_depth_ratio: float = 0.25
    stagnant_pnl_abs_usd: float = 0.015

    paper_mode: bool = True
    execution_enabled: bool = False
    kalshi_api_base: Optional[str] = None

    session_restrict_et: bool = False
    session_et_weekdays_only: bool = True
    session_et_start_hour: int = 9
    session_et_start_minute: int = 0
    session_et_end_hour: int = 16
    session_et_end_minute: int = 0

    sp_series_tickers: Tuple[str, ...] = field(
        default_factory=lambda: (
            "KXINX",
            "KXNDX",
            "INXD",
            "NASDAQ",
            "KXNASDAQ",
            "KXSP500",
            "INX",
        )
    )
    btc_series_tickers: Tuple[str, ...] = field(
        default_factory=lambda: (
            "KXBTC15",
            "KXBTCUSD",
            "KXBTCZ",
            "KXBTCD",
            "KXBTC",
            "BTCUSD",
            "BTC15",
            "BTCZ",
            "BTC",
        )
    )
    eth_series_tickers: Tuple[str, ...] = field(
        default_factory=lambda: (
            "KXETH15",
            "KXETHD",
            "KXETH",
            "ETHUSD",
            "ETH",
        )
    )

    @classmethod
    def from_env(cls) -> "KalshiScalpConfig":
        """Load config with ``KALSHI_SCALP_*`` and demo base URL defaults."""
        base = (os.environ.get("KALSHI_API_BASE") or os.environ.get("KALSHI_TRADE_API_BASE") or "").strip() or None
        pt = _env_float("KALSHI_SCALP_PROFIT_TARGET_USD", 0.04)
        sl = _env_float("KALSHI_SCALP_STOP_LOSS_USD", -0.04)
        return cls(
            deployment_per_trade_usd=_env_float("KALSHI_SCALP_DEPLOYMENT_USD", 2.0),
            default_profit_target_dollars=pt,
            profit_target_range_dollars=(
                _env_float("KALSHI_SCALP_PROFIT_MIN_USD", 0.03),
                _env_float("KALSHI_SCALP_PROFIT_MAX_USD", 0.08),
            ),
            default_stop_loss_dollars=sl,
            stop_loss_range_dollars=(
                _env_float("KALSHI_SCALP_STOP_MIN_USD", -0.05),
                _env_float("KALSHI_SCALP_STOP_MAX_USD", -0.03),
            ),
            scanner_interval_seconds=_env_float("KALSHI_SCALP_SCANNER_INTERVAL_SEC", 20.0),
            position_check_interval_seconds=_env_float("KALSHI_SCALP_POSITION_CHECK_SEC", 5.0),
            soft_timeout_seconds=_env_float("KALSHI_SCALP_SOFT_TIMEOUT_SEC", 60.0),
            hard_timeout_seconds=_env_float("KALSHI_SCALP_HARD_TIMEOUT_SEC", 120.0),
            max_open_positions=max(1, _env_int("KALSHI_SCALP_MAX_OPEN", 1)),
            max_trade_attempts_per_hour=max(1, _env_int("KALSHI_SCALP_MAX_ATTEMPTS_PER_HOUR", 12)),
            max_completed_trades_per_hour=max(1, _env_int("KALSHI_SCALP_MAX_COMPLETES_PER_HOUR", 8)),
            daily_loss_limit_usd=_parse_daily_loss_limit(),
            max_consecutive_losses=max(1, _env_int("KALSHI_SCALP_MAX_CONSECUTIVE_LOSSES", 5)),
            min_volume_fp=_env_float("KALSHI_SCALP_MIN_VOLUME_FP", 1.0),
            max_spread_prob=_env_float("KALSHI_SCALP_MAX_SPREAD", 0.06),
            min_top_of_book_contracts=_env_float("KALSHI_SCALP_MIN_BOOK", 5.0),
            emergency_depth_ratio=_env_float("KALSHI_SCALP_EMERGENCY_DEPTH_RATIO", 0.25),
            stagnant_pnl_abs_usd=_env_float("KALSHI_SCALP_STAGNANT_USD", 0.015),
            paper_mode=_env_bool("KALSHI_SCALP_PAPER_MODE", True),
            execution_enabled=_env_bool("KALSHI_SCALP_EXECUTION_ENABLED", False),
            kalshi_api_base=base,
            session_restrict_et=_env_bool("KALSHI_SCALP_SESSION_ET_ONLY", False),
            session_et_weekdays_only=_env_bool("KALSHI_SCALP_SESSION_WEEKDAYS", True),
            session_et_start_hour=_env_int("KALSHI_SCALP_SESSION_START_H", 9),
            session_et_start_minute=_env_int("KALSHI_SCALP_SESSION_START_M", 0),
            session_et_end_hour=_env_int("KALSHI_SCALP_SESSION_END_H", 16),
            session_et_end_minute=_env_int("KALSHI_SCALP_SESSION_END_M", 0),
        )

    def series_for_families(self) -> FrozenSet[str]:
        """Series roots to scan for eligible markets."""
        parts: list[str] = []
        fams = {f.strip().upper() for f in self.allowed_market_families}
        if "S&P" in fams or "SP" in fams:
            parts.extend(self.sp_series_tickers)
        if "BTC" in fams:
            parts.extend(self.btc_series_tickers)
        if "ETH" in fams:
            parts.extend(self.eth_series_tickers)
        return frozenset(s.upper() for s in parts)
