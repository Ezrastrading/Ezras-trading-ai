"""
Fill interpretation lock: Coinbase may return ``size`` as quote (USD) or base (BTC/ETH).

Hard abort on internal inconsistency — no trade continues on mismatch.
"""

from __future__ import annotations

import logging
from typing import Any, List, Mapping, Tuple, Union

logger = logging.getLogger(__name__)

FillLike = Union[Mapping[str, Any], Any]


class FillMismatchAbort(Exception):
    """Raised when base × price cannot be reconciled with quote leg within tolerance."""

    code = "FILL_MISMATCH_ABORT"


def _f(fill: FillLike, key: str, default: float = 0.0) -> float:
    if isinstance(fill, Mapping):
        raw = fill.get(key)
    else:
        raw = getattr(fill, key, None)
        if raw is None and key == "size":
            raw = getattr(fill, "filled_size", None)
    try:
        return float(raw if raw is not None else default)
    except (TypeError, ValueError):
        return default


def normalize_fill(fill: FillLike) -> Tuple[float, float, float]:
    """
    Return ``(base_size, quote_value, price)`` for one execution leg.

    If ``size`` ≈ ``filled_value`` (quote notional), treat ``size`` as USD and derive base.
    Otherwise treat ``size`` as base; quote leg is ``filled_value`` when present else ``size × price``.

    HARD ASSERT: ``|base × price - ref_quote| <= 2%`` of ``ref_quote`` (API quote when set).
    """
    price = abs(_f(fill, "price") or _f(fill, "average_filled_price"))
    size = abs(_f(fill, "size") or _f(fill, "filled_size"))
    quote = abs(
        _f(fill, "filled_value")
        or _f(fill, "value")
        or _f(fill, "quote_value")
    )

    if price <= 0:
        raise FillMismatchAbort("FILL_MISMATCH_ABORT: non-positive price")

    # If size ≈ quote → it's USD notional for the leg, not base
    if quote > 0 and abs(size - quote) / max(quote, 1e-9) < 0.02:
        base_size = quote / price
    else:
        base_size = size

    implied = base_size * price
    ref_quote = quote if quote > 0 else implied
    hard_tol = max(ref_quote * 0.02, 1e-8)
    if abs(implied - ref_quote) > hard_tol:
        raise FillMismatchAbort("FILL_MISMATCH_ABORT")

    quote_value = ref_quote
    return base_size, quote_value, price


def aggregate_fills_to_stats(fills: List[Mapping[str, Any]]) -> Tuple[float, float, float]:
    """
    Sum normalized legs: total base, volume-weighted avg price, total fees.

    Returns ``(total_base, avg_price, total_fee_usd)``.
    """
    total_base = 0.0
    total_quote = 0.0
    total_fee = 0.0
    for raw in fills:
        if not isinstance(raw, Mapping):
            continue
        b, q, _px = normalize_fill(raw)
        total_base += b
        total_quote += q
        total_fee += abs(_f(raw, "commission") or _f(raw, "fee"))
    avg = total_quote / total_base if total_base > 0 else 0.0
    return total_base, avg, total_fee
