"""Normalized trade legs — base vs quote invariants and position safety."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional, Tuple, overload

from trading_ai.organism.types import InstrumentKind

logger = logging.getLogger(__name__)


class TradeTruthError(RuntimeError):
    """Raised when base/quote legs are inconsistent or unsafe."""


def assert_valid_base_quote(base_size: Any, quote_size: Any, price: Any) -> None:
    """
    Hard invariant: ``base_size`` is asset amount, ``quote_size`` is money (quote leg).
    Rejects missing components and relative mismatch beyond 5% on the quote leg.
    """
    if base_size is None or quote_size is None or price is None:
        raise ValueError("Missing trade components")
    b = float(base_size)
    q = float(quote_size)
    p = float(price)
    if b <= 0 or q < 0 or p <= 0:
        raise ValueError("Non-positive base/price or negative quote")
    expected_quote = b * p
    denom = max(abs(q), 1e-9)
    if abs(expected_quote - q) / denom > 0.05:
        raise ValueError("Base/Quote mismatch beyond tolerance")


def log_execution_truth_record(
    *,
    base_size: float,
    quote_size: float,
    price: float,
    source: str = "fill_parser",
) -> None:
    """Structured CRITICAL log for execution truth (base vs quote vs price)."""
    logger.critical(
        "execution_base_quote_truth %s",
        json.dumps(
            {"base_size": base_size, "quote_size": quote_size, "price": price, "source": source},
            default=str,
        ),
    )


def _tol(quote: float) -> float:
    q = abs(float(quote))
    return max(1e-9, 1e-6 * q)


def check_base_quote_match(
    base_qty: float,
    price: float,
    quote_qty: float,
    *,
    context: str = "",
) -> None:
    """
    Enforce |base * price - quote| <= tolerance.

    Use one side of a fill (buy or sell) with the reported average price and quote notional.
    """
    b = float(base_qty)
    p = float(price)
    q = float(quote_qty)
    lhs = abs(b * p)
    rhs = abs(q)
    diff = abs(lhs - rhs)
    if diff > _tol(rhs) and diff > _tol(lhs):
        msg = (
            f"BASE_QUOTE_MISMATCH: |base*price - quote|={diff} (base={b}, price={p}, quote={q})"
            + (f" [{context}]" if context else "")
        )
        raise TradeTruthError(msg)


def validate_trade_truth(raw: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    If organism fields are present, validate invariants. Returns (ok, error_code).

    Does not require all fields — when ``instrument_kind`` is set, required legs must be coherent.
    """
    kind = str(raw.get("instrument_kind") or "").strip().lower()
    if not kind or kind == "unknown":
        return True, None
    try:
        if kind == InstrumentKind.SPOT.value:
            bq = raw.get("base_qty")
            ap = raw.get("avg_entry_price")
            qb = raw.get("quote_qty_buy")
            if bq is not None and ap is not None and qb is not None:
                check_base_quote_match(float(bq), float(ap), float(qb), context="entry")
            apx = raw.get("avg_exit_price")
            qs = raw.get("quote_qty_sell")
            if bq is not None and apx is not None and qs is not None:
                check_base_quote_match(float(bq), float(apx), float(qs), context="exit")
        elif kind == InstrumentKind.PREDICTION.value:
            c = raw.get("contracts")
            ep = raw.get("entry_price_per_contract")
            quote_in = raw.get("quote_qty_buy")
            if c is not None and ep is not None and quote_in is not None:
                check_base_quote_match(float(c), float(ep), float(quote_in), context="prediction_entry")
        elif kind == InstrumentKind.OPTIONS.value:
            c = raw.get("contracts")
            ep = raw.get("entry_premium")
            prem = raw.get("quote_qty_buy")
            if c is not None and ep is not None and prem is not None:
                check_base_quote_match(float(c), float(ep), float(prem), context="options_entry")
    except TradeTruthError as e:
        return False, str(e)
    return True, None


@overload
def assert_no_oversell(*, position_base_before: float, sell_base_qty: float) -> None: ...


@overload
def assert_no_oversell(__a: float, __b: float) -> None: ...


def assert_no_oversell(
    *args: Any,
    position_base_before: Optional[float] = None,
    sell_base_qty: Optional[float] = None,
) -> None:
    """Fail if closing/selling more base than held. Use ``(pos, sell)`` or keyword args."""
    if len(args) == 2:
        pb = float(args[0])
        sb = float(args[1])
    elif position_base_before is not None and sell_base_qty is not None:
        pb = float(position_base_before)
        sb = float(sell_base_qty)
    else:
        raise TypeError(
            "assert_no_oversell(position_base, sell_base) or "
            "assert_no_oversell(position_base_before=..., sell_base_qty=...)"
        )
    if sb < 0:
        raise TradeTruthError(f"NO_NEGATIVE_SELL: sell_base_qty={sb}")
    if sb > pb + 1e-12:
        raise TradeTruthError(f"OVERSELL: sell_base_qty={sb} > position={pb}")


def base_quote_residual(base_qty: float, price: float, quote_qty: float) -> float:
    """Absolute residual for diagnostics."""
    return abs(float(base_qty) * float(price) - float(quote_qty))


def is_anomaly_residual(base_qty: float, price: float, quote_qty: float) -> bool:
    q = abs(float(quote_qty))
    diff = base_quote_residual(base_qty, price, quote_qty)
    return diff > _tol(q) and (q <= 0 or diff / q > 1e-4)


def abort_if_mismatch(base_qty: float, price: float, quote_qty: float, *, label: str = "") -> None:
    """Strict abort used at execution boundaries."""
    if is_anomaly_residual(base_qty, price, quote_qty):
        raise TradeTruthError(f"BASE_QUOTE_MISMATCH{(':' + label) if label else ''}")
