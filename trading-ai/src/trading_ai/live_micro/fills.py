from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _f(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_coinbase_fills(
    fills: List[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Dict[str, Any]]:
    """
    Parse Coinbase Advanced Trade fills.

    Coinbase fills may report ``size`` in **quote** when ``size_in_quote`` is true.

    Returns (avg_price, base_qty, quote_amount, commission_quote, diagnostics)
    """
    total_base = 0.0
    total_quote = 0.0
    total_commission = 0.0
    saw_quote_sizes = 0
    saw_base_sizes = 0

    for f in list(fills or []):
        if not isinstance(f, dict):
            continue
        price = _f(f.get("price") or f.get("fill_price") or f.get("trade_price"))
        size = _f(f.get("size") or f.get("filled_size") or f.get("base_size"))
        comm = _f(f.get("commission") or f.get("fee") or f.get("fees"))
        total_commission += max(0.0, comm)
        if price <= 0 or size <= 0:
            continue

        size_in_quote = f.get("size_in_quote")
        if isinstance(size_in_quote, bool) and size_in_quote:
            # size is quote currency (USD/USDC); derive base from quote/price
            saw_quote_sizes += 1
            total_quote += size
            total_base += (size / price)
        else:
            saw_base_sizes += 1
            total_base += size
            total_quote += price * size

    if total_base <= 0 or total_quote <= 0:
        return None, None, None, None, {
            "fills_n": len(list(fills or [])),
            "reason": "no_valid_fill_rows",
            "saw_quote_sizes": saw_quote_sizes,
            "saw_base_sizes": saw_base_sizes,
        }

    avg = total_quote / total_base
    return (
        avg,
        total_base,
        total_quote,
        total_commission if total_commission > 0 else 0.0,
        {
            "fills_n": len(list(fills or [])),
            "saw_quote_sizes": saw_quote_sizes,
            "saw_base_sizes": saw_base_sizes,
        },
    )

