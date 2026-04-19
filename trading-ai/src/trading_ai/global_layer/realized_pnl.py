"""
Unified **realized** PnL computation — truth layer (no strategy decisions).

Dispatch on ``instrument_kind``:
- ``spot`` — Coinbase-style: gross = sell_quote - buy_quote, net = gross - fees
- ``prediction`` — binary-style: net = (payout * contracts) - (entry_price * contracts) - fees
- ``options`` — net = (exit_value - entry_value) * contracts * multiplier - fees

Missing inputs → explicit ``unknown`` / ``None``; callers must not treat as zero profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@dataclass
class RealizedPnlResult:
    instrument_kind: str
    net_pnl: Optional[float]
    gross_pnl: Optional[float]
    pnl_sign: str  # "profit" | "loss" | "flat" | "unknown"
    return_pct: Optional[float]
    return_bps: Optional[float]
    buy_quote_spent: Optional[float]
    sell_quote_received: Optional[float]
    total_fees: Optional[float]
    fields_known: Dict[str, bool] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def _sign_from_net(net: Optional[float]) -> str:
    if net is None:
        return "unknown"
    if net > 1e-12:
        return "profit"
    if net < -1e-12:
        return "loss"
    return "flat"


def _spot_realized(trade: Mapping[str, Any]) -> RealizedPnlResult:
    bq = _f(trade.get("buy_quote_spent"))
    sq = _f(trade.get("sell_quote_received"))
    fees = _f(trade.get("fees_total"))
    complete = bool(trade.get("fields_complete", bq is not None and sq is not None and fees is not None))

    known = {
        "buy_quote_spent": bq is not None,
        "sell_quote_received": sq is not None,
        "total_fees": fees is not None,
    }
    notes: List[str] = []

    if not complete or bq is None or sq is None:
        return RealizedPnlResult(
            instrument_kind="spot",
            net_pnl=None,
            gross_pnl=None,
            pnl_sign="unknown",
            return_pct=None,
            return_bps=None,
            buy_quote_spent=bq,
            sell_quote_received=sq,
            total_fees=fees,
            fields_known=known,
            notes=["incomplete_spot_inputs"],
        )

    gross = sq - bq
    fee_val = fees if fees is not None else 0.0
    net = gross - fee_val
    ret_pct = (net / bq) if bq != 0 else None
    ret_bps = (ret_pct * 10000.0) if ret_pct is not None else None

    return RealizedPnlResult(
        instrument_kind="spot",
        net_pnl=net,
        gross_pnl=gross,
        pnl_sign=_sign_from_net(net),
        return_pct=ret_pct,
        return_bps=ret_bps,
        buy_quote_spent=bq,
        sell_quote_received=sq,
        total_fees=fees,
        fields_known=known,
        notes=notes,
    )


def _prediction_realized(trade: Mapping[str, Any]) -> RealizedPnlResult:
    """Binary prediction market: payout 0 or 1 per contract; entry_price in quote per contract."""
    contracts = _f(trade.get("contracts"))
    entry_px = _f(trade.get("entry_price"))
    payout = _f(trade.get("payout"))  # 0 or 1 typically
    fees = _f(trade.get("fees_total")) or 0.0

    if contracts is None or entry_px is None or payout is None:
        return RealizedPnlResult(
            instrument_kind="prediction",
            net_pnl=None,
            gross_pnl=None,
            pnl_sign="unknown",
            return_pct=None,
            return_bps=None,
            buy_quote_spent=entry_px,
            sell_quote_received=None,
            total_fees=_f(trade.get("fees_total")),
            fields_known={"contracts": contracts is not None, "entry_price": entry_px is not None},
            notes=["incomplete_prediction_inputs"],
        )

    gross = payout * contracts - entry_px * contracts
    net = gross - fees
    cost = entry_px * contracts
    ret_pct = (net / cost) if cost and cost != 0 else None

    return RealizedPnlResult(
        instrument_kind="prediction",
        net_pnl=net,
        gross_pnl=gross,
        pnl_sign=_sign_from_net(net),
        return_pct=ret_pct,
        return_bps=(ret_pct * 10000.0) if ret_pct is not None else None,
        buy_quote_spent=entry_px * contracts,
        sell_quote_received=payout * contracts,
        total_fees=fees,
        fields_known={"complete": True},
        notes=[],
    )


def _options_realized(trade: Mapping[str, Any]) -> RealizedPnlResult:
    contracts = _f(trade.get("contracts"))
    mult = _f(trade.get("multiplier"))
    entry_val = _f(trade.get("entry_value"))
    exit_val = _f(trade.get("exit_value"))
    fees = _f(trade.get("fees_total")) or 0.0

    if None in (contracts, mult, entry_val, exit_val):
        return RealizedPnlResult(
            instrument_kind="options",
            net_pnl=None,
            gross_pnl=None,
            pnl_sign="unknown",
            return_pct=None,
            return_bps=None,
            buy_quote_spent=None,
            sell_quote_received=None,
            total_fees=_f(trade.get("fees_total")),
            fields_known={"complete": False},
            notes=["incomplete_options_inputs"],
        )

    gross = (exit_val - entry_val) * contracts * mult
    net = gross - fees
    entry_cost = entry_val * contracts * mult
    ret_pct = (net / entry_cost) if entry_cost and entry_cost != 0 else None

    return RealizedPnlResult(
        instrument_kind="options",
        net_pnl=net,
        gross_pnl=gross,
        pnl_sign=_sign_from_net(net),
        return_pct=ret_pct,
        return_bps=(ret_pct * 10000.0) if ret_pct is not None else None,
        buy_quote_spent=entry_cost,
        sell_quote_received=exit_val * contracts * mult,
        total_fees=fees,
        fields_known={"complete": True},
        notes=[],
    )


def compute_expectancy(
    *,
    win_rate: float,
    avg_win: float,
    loss_rate: float,
    avg_loss: float,
) -> float:
    """
    Discrete expectancy: ``win_rate * avg_win - loss_rate * avg_loss`` (same units as PnL inputs).

    Truth helper for monitoring — not a strategy signal.
    """
    return float(win_rate) * float(avg_win) - float(loss_rate) * float(avg_loss)


def compute_realized_pnl(trade: Mapping[str, Any]) -> RealizedPnlResult:
    """
    Single entry point for closed-trade realized PnL.

    ``trade`` should include ``instrument_kind`` in {``spot``, ``prediction``, ``options``}.
    Unknown kind → result with ``pnl_sign=unknown`` and notes.
    """
    kind = str(trade.get("instrument_kind") or "").strip().lower()
    if kind == "spot":
        return _spot_realized(trade)
    if kind in ("prediction", "kalshi", "polymarket"):
        t2 = dict(trade)
        t2["instrument_kind"] = "prediction"
        return _prediction_realized(t2)
    if kind == "options":
        return _options_realized(trade)
    return RealizedPnlResult(
        instrument_kind=kind or "unknown",
        net_pnl=None,
        gross_pnl=None,
        pnl_sign="unknown",
        return_pct=None,
        return_bps=None,
        buy_quote_spent=None,
        sell_quote_received=None,
        total_fees=None,
        fields_known={},
        notes=["unknown_instrument_kind"],
    )
