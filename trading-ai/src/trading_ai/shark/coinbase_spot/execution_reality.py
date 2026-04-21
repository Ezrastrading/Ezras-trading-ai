"""Slippage / tiering helpers for Gate B staged PnL summaries (deterministic, non-venue)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class AssetTier(str, Enum):
    BTC_ETH = "btc_eth"
    LOW_CAP = "low_cap"
    MAJORS = "majors"


def infer_asset_tier(product_id: str, *, liquidity_score: float | None = None) -> AssetTier:
    pid = (product_id or "").upper()
    if "BTC" in pid or "ETH" in pid:
        return AssetTier.BTC_ETH
    if liquidity_score is not None and liquidity_score < 0.55:
        return AssetTier.LOW_CAP
    return AssetTier.MAJORS


def midpoint_slippage_bps(tier: AssetTier) -> float:
    if tier == AssetTier.LOW_CAP:
        return 35.0
    if tier == AssetTier.BTC_ETH:
        return 8.0
    return 15.0


def simulate_execution_prices(
    *,
    intended_entry_price: float,
    intended_exit_price: float,
    tier: AssetTier,
) -> Dict[str, float]:
    slip = midpoint_slippage_bps(tier) * 1e-4
    actual_entry = float(intended_entry_price) * (1.0 + slip)
    actual_exit = float(intended_exit_price) * (1.0 - slip)
    return {
        "intended_entry_price": float(intended_entry_price),
        "intended_exit_price": float(intended_exit_price),
        "actual_entry_price": actual_entry,
        "actual_exit_price": actual_exit,
        "tier": tier.value,
    }


def theoretical_vs_actual_roundtrip_pnl_usd(
    *,
    base_qty: float,
    intended_entry: float,
    intended_exit: float,
    actual_entry: float,
    actual_exit: float,
    fees_usd: float,
) -> Dict[str, float]:
    theo = base_qty * (intended_exit - intended_entry)
    actual_gross = base_qty * (actual_exit - actual_entry)
    actual_net = actual_gross - fees_usd
    return {
        "theoretical_gross_pnl_usd": theo,
        "actual_gross_pnl_usd": actual_gross,
        "actual_net_pnl_usd": actual_net,
    }
