"""Realized PnL by instrument — no placeholders; explicit inputs per kind."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from trading_ai.organism.types import InstrumentKind


@dataclass(frozen=True)
class RealizedPnLResult:
    net_pnl: float
    pnl_sign: int  # -1, 0, 1
    return_pct: Optional[float]
    return_bps: Optional[float]
    gross_pnl: float


def _sign(x: float) -> int:
    if x > 1e-15:
        return 1
    if x < -1e-15:
        return -1
    return 0


def _ret(net: float, notional: float) -> tuple[Optional[float], Optional[float]]:
    if notional is None:
        return None, None
    n = float(notional)
    if abs(n) < 1e-15 or math.isnan(n):
        return None, None
    rp = net / n
    return rp, rp * 10000.0


def compute_realized_pnl(
    *,
    instrument_kind: str,
    fees: float,
    # spot
    quote_qty_buy: Optional[float] = None,
    quote_qty_sell: Optional[float] = None,
    # prediction (binary / contracts)
    contracts: Optional[float] = None,
    entry_price_per_contract: Optional[float] = None,
    payout_per_contract: Optional[float] = None,
    # options
    entry_premium_per_contract: Optional[float] = None,
    exit_premium_per_contract: Optional[float] = None,
    option_multiplier: float = 100.0,
) -> RealizedPnLResult:
    """
    Realized net PnL after fees.

    SPOT: gross = sell_quote - buy_quote; net = gross - fees.

    PREDICTION: net = (payout_per_contract - entry_price_per_contract) * contracts - fees
    (prices in quote per contract; payout is realized value per contract at settlement/exit).

    OPTIONS: net = (exit - entry) * contracts * multiplier - fees (premiums per contract).
    """
    fees_f = float(fees)
    kind = (instrument_kind or "").strip().lower()

    if kind == InstrumentKind.SPOT.value:
        if quote_qty_buy is None or quote_qty_sell is None:
            raise ValueError("spot requires quote_qty_buy and quote_qty_sell")
        qb = float(quote_qty_buy)
        qs = float(quote_qty_sell)
        gross = qs - qb
        net = gross - fees_f
        notional = abs(qb) if abs(qb) >= abs(qs) else abs(qs)
        rp, rbps = _ret(net, notional)
        return RealizedPnLResult(
            net_pnl=net,
            pnl_sign=_sign(net),
            return_pct=rp,
            return_bps=rbps,
            gross_pnl=gross,
        )

    if kind == InstrumentKind.PREDICTION.value:
        if contracts is None or entry_price_per_contract is None or payout_per_contract is None:
            raise ValueError("prediction requires contracts, entry_price_per_contract, payout_per_contract")
        c = float(contracts)
        net = (float(payout_per_contract) - float(entry_price_per_contract)) * c - fees_f
        gross = (float(payout_per_contract) - float(entry_price_per_contract)) * c
        notional = abs(float(entry_price_per_contract) * c)
        rp, rbps = _ret(net, notional)
        return RealizedPnLResult(
            net_pnl=net,
            pnl_sign=_sign(net),
            return_pct=rp,
            return_bps=rbps,
            gross_pnl=gross,
        )

    if kind == InstrumentKind.OPTIONS.value:
        if contracts is None or entry_premium_per_contract is None or exit_premium_per_contract is None:
            raise ValueError("options requires contracts, entry_premium_per_contract, exit_premium_per_contract")
        c = float(contracts)
        m = float(option_multiplier)
        gross = (float(exit_premium_per_contract) - float(entry_premium_per_contract)) * c * m
        net = gross - fees_f
        notional = abs(float(entry_premium_per_contract) * c * m)
        rp, rbps = _ret(net, notional)
        return RealizedPnLResult(
            net_pnl=net,
            pnl_sign=_sign(net),
            return_pct=rp,
            return_bps=rbps,
            gross_pnl=gross,
        )

    raise ValueError(f"unknown instrument_kind: {instrument_kind!r}")
