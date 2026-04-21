"""Single-leg Coinbase spot product_id → base/quote (no multi-hop routing)."""

from __future__ import annotations

from typing import Tuple

# Known Coinbase Advanced Trade spot quote segments (extend as venue adds pairs).
_KNOWN_QUOTE_SUFFIXES = ("USDC", "USD", "EUR", "GBP", "USDT")


def parse_spot_base_quote(product_id: str) -> Tuple[str, str]:
    """
    Parse ``BASE-QUOTE`` (e.g. ``BTC-USD``, ``ETH-USDC``, ``SOL-EUR``).

    Returns ``(base_asset, quote_asset)`` uppercased. Falls back to last ``-`` segment as quote.
    """
    raw = (product_id or "").strip().upper()
    if not raw or "-" not in raw:
        return raw or "UNKNOWN", "USD"
    for suf in _KNOWN_QUOTE_SUFFIXES:
        if raw.endswith("-" + suf):
            base = raw[: -(len(suf) + 1)]
            return base, suf
    base, _, quote = raw.rpartition("-")
    return base or raw, quote or "USD"


def is_spot_like_product_id(product_id: str) -> bool:
    s = (product_id or "").strip().upper()
    return "-" in s and len(s) >= 5

