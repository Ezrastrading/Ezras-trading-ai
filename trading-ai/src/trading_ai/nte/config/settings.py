"""NTE Coinbase configuration — fixed risk bands from system spec."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

_DEFAULT_NTE_PRODUCTS: Tuple[str, ...] = (
    "BTC-USD",
    "BTC-USDC",
    "ETH-USD",
    "ETH-USDC",
    "SOL-USD",
    "SOL-USDC",
    "AVAX-USD",
    "LINK-USD",
)


def _nte_products_from_environ() -> Tuple[str, ...]:
    raw = (os.environ.get("NTE_PRODUCTS") or os.environ.get("NTE_COINBASE_PRODUCTS") or "").strip()
    if not raw:
        return _DEFAULT_NTE_PRODUCTS
    parts = tuple(p.strip().upper() for p in raw.split(",") if p.strip())
    return parts if parts else _DEFAULT_NTE_PRODUCTS


@dataclass(frozen=True)
class NTECoinbaseSettings:
    products: Tuple[str, ...] = _DEFAULT_NTE_PRODUCTS
    # Position: 15–25% per trade, max 2 concurrent
    size_pct_min: float = 0.15
    size_pct_max: float = 0.25
    max_open_positions: int = 2
    # Exits (fractions, e.g. 0.005 = 0.5%)
    tp_min: float = 0.005
    tp_max: float = 0.012
    sl_min: float = 0.004
    sl_max: float = 0.008
    time_stop_min_sec: int = 180
    time_stop_max_sec: int = 360
    trail_trigger: float = 0.005  # after +0.5%
    trail_lock_min: float = 0.002
    trail_lock_max: float = 0.003
    # Risk — Avenue 1 default hard cap 4% daily (see coinbase_avenue1_launch)
    daily_loss_min: float = 0.04
    daily_loss_max: float = 0.04
    max_consecutive_losses_pause: int = 4
    # Filters
    max_spread_pct: float = 0.0012  # 0.12% — tight book
    spike_block_pct: float = 0.004  # 0.4% vs short MA = chaos
    min_quote_volume_24h: float = 5_000_000.0
    # Execution — limit-first (maker); market reserved for exits
    entry_limit_offset_bps: float = 5.0  # buy below mid / maker patience
    stale_pending_order_sec: float = 75.0
    max_volatility_bps: float = 25.0  # classifier — above = chaotic
    post_only_limits: bool = True
    # Avenue id for multi-avenue memory
    avenue_id: str = "coinbase"


def load_nte_settings() -> NTECoinbaseSettings:
    dl = float(os.environ.get("NTE_DAILY_LOSS_CAP_PCT", os.environ.get("NTE_AVE1_DAILY_LOSS_PCT", "0.04")))
    return NTECoinbaseSettings(
        products=_nte_products_from_environ(),
        size_pct_min=float(os.environ.get("NTE_SIZE_PCT_MIN", "0.15")),
        size_pct_max=float(os.environ.get("NTE_SIZE_PCT_MAX", "0.25")),
        max_spread_pct=float(os.environ.get("NTE_MAX_SPREAD_PCT", "0.0012")),
        spike_block_pct=float(os.environ.get("NTE_SPIKE_BLOCK_PCT", "0.004")),
        entry_limit_offset_bps=float(os.environ.get("NTE_LIMIT_ENTRY_OFFSET_BPS", "5")),
        stale_pending_order_sec=float(os.environ.get("NTE_STALE_PENDING_SEC", "45")),
        max_volatility_bps=float(os.environ.get("NTE_MAX_VOLATILITY_BPS", "22")),
        post_only_limits=(os.environ.get("NTE_POST_ONLY", "true").strip().lower()
                          in ("1", "true", "yes")),
        daily_loss_min=dl,
        daily_loss_max=dl,
    )


def _default_nte_coinbase_products() -> Tuple[str, ...]:
    """Code-default product universe (ignores env overrides — used for policy diff vs ``NTE_PRODUCTS``)."""
    return _DEFAULT_NTE_PRODUCTS
