"""Cross-venue ticker resolution (minimal stub for Kalshi execution path)."""
from __future__ import annotations

from typing import Any, Optional


def resolve_kalshi_ticker(settings: Any, market: Any) -> Optional[str]:
    """Return a Kalshi ticker string when derivable from ``market``; otherwise None."""
    for attr in ("kalshi_ticker", "kalshi_market_ticker", "ticker"):
        v = getattr(market, attr, None)
        if v:
            return str(v)
    if isinstance(market, dict):
        for k in ("kalshi_ticker", "kalshi_market_ticker", "ticker", "market_id"):
            v = market.get(k)
            if v:
                return str(v)
    mid = getattr(market, "market_id", None)
    return str(mid) if mid else None
