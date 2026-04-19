"""Base-size formatting for Coinbase spot sells (used by smoke tests + NTE)."""

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal
from typing import Dict

logger = logging.getLogger(__name__)

_PRODUCT_MIN_BASE_SIZE: Dict[str, float] = {
    "DOGE-USD": 0.1,
    "ADA-USD": 1.0,
    "XRP-USD": 1.0,
    "SHIB-USD": 1000.0,
    "PEPE-USD": 1000.0,
    "BTC-USD": 0.000001,
    "ETH-USD": 0.00000001,
    "SOL-USD": 0.000001,
    "AVAX-USD": 0.001,
    "DOT-USD": 0.1,
    "LINK-USD": 0.01,
    "UNI-USD": 0.01,
    "MATIC-USD": 1.0,
}

_PRODUCT_BASE_INCREMENT: Dict[str, float] = {}

_PRODUCT_BASE_PRECISION: Dict[str, int] = {
    "BTC-USD": 8,
    "ETH-USD": 8,
    "SOL-USD": 2,
    "DOGE-USD": 1,
    "ADA-USD": 6,
    "XRP-USD": 6,
    "LINK-USD": 4,
    "DOT-USD": 2,
    "AVAX-USD": 4,
    "UNI-USD": 4,
    "MATIC-USD": 2,
    "SHIB-USD": 0,
    "PEPE-USD": 0,
}


def _min_base_size_for_product(pid: str) -> float:
    return float(_PRODUCT_MIN_BASE_SIZE.get(pid, 0.000001))


def _enforce_min_base_for_sell(pid: str, base_size: float) -> float:
    if base_size <= 0:
        return 0.0
    min_sz = _min_base_size_for_product(pid)
    if base_size + 1e-18 < min_sz:
        logger.warning(
            "Position base size %s < exchange min order %s for %s — skip sell",
            base_size,
            min_sz,
            pid,
        )
        return 0.0
    return base_size


def _fmt_base_size(product_id: str, base_size: float) -> str:
    precision = int(_PRODUCT_BASE_PRECISION.get(product_id, 8))
    q = Decimal("1").scaleb(-precision)
    d = Decimal(str(base_size)).quantize(q, rounding=ROUND_DOWN)
    inc = float(_PRODUCT_BASE_INCREMENT.get(product_id) or 0.0)
    if inc > 0:
        inc_d = Decimal(str(inc))
        if inc_d > 0:
            n = (d / inc_d).quantize(Decimal(1), rounding=ROUND_DOWN)
            d = (n * inc_d).quantize(q, rounding=ROUND_DOWN)
    return format(d, f".{precision}f")
