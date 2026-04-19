"""
Coinbase Advanced Trade spot product constraints — validate before live orders.

Values are conservative defaults; refresh from ``GET /market/products/{product_id}`` in prod.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple

# Defaults when REST product metadata not cached (tighten after fetching live).
_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "BTC-USD": {
        "min_base": Decimal("0.00001"),
        "base_increment": Decimal("0.00000001"),
        "min_notional_usd": Decimal("10"),
    },
    "ETH-USD": {
        "min_base": Decimal("0.0001"),
        "base_increment": Decimal("0.00000001"),
        "min_notional_usd": Decimal("10"),
    },
}


def _d(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


def validate_order_size(
    product_id: str,
    *,
    base_size: Optional[str] = None,
    quote_notional_usd: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Return (ok, reason_if_bad). Invalid precision / below minimum → reject.
    """
    pid = (product_id or "").strip().upper()
    meta = _DEFAULTS.get(pid)
    if not meta:
        return True, None
    min_b = meta["min_base"]
    inc = meta["base_increment"]
    min_n = meta["min_notional_usd"]

    if quote_notional_usd is not None:
        if _d(quote_notional_usd) < min_n:
            return False, f"quote_below_min_notional_{min_n}"

    if base_size is not None and str(base_size).strip():
        try:
            raw = _d(base_size)
        except Exception:
            return False, "base_size_parse"
        if raw < min_b:
            return False, f"base_below_min_{min_b}"
        # increment: remainder mod inc should be ~0
        if inc > 0:
            q = (raw / inc).quantize(Decimal("1"), rounding=ROUND_DOWN) * inc
            if abs(raw - q) > inc * Decimal("1e-6"):
                return False, "base_increment_mismatch"
    return True, None


def round_base_to_increment(product_id: str, base_float: float) -> str:
    """Round down to product increment for string submission."""
    pid = product_id.strip().upper()
    meta = _DEFAULTS.get(pid) or _DEFAULTS["BTC-USD"]
    inc: Decimal = meta["base_increment"]
    b = Decimal(str(base_float))
    steps = (b / inc).quantize(Decimal("1"), rounding=ROUND_DOWN)
    out = steps * inc
    s = format(out, "f")
    if "BTC" in pid:
        return s.rstrip("0").rstrip(".") if "." in s else s
    return s.rstrip("0").rstrip(".") if "." in s else s
