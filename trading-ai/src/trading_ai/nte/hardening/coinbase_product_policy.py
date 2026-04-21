"""
Canonical Coinbase product allowance — shared by live order guard and validation selectors.

**Single source of truth** for “is this product_id permitted for live trading” matches
:func:`assert_live_order_permitted` (NTE settings ``products`` tuple).
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Tuple

from trading_ai.nte.config.settings import _default_nte_coinbase_products, load_nte_settings


def coinbase_product_nte_allowed(product_id: str) -> bool:
    """
    True if ``product_id`` may be traded live on Coinbase under current NTE config.

    Same rule as the former private ``_product_allowed`` in ``live_order_guard``:
    ``*`` / empty passes; otherwise ``product_id`` must appear in ``load_nte_settings().products``.
    """
    s = load_nte_settings()
    pid = (product_id or "").strip()
    if pid == "*" or not pid:
        return True
    return pid in set(s.products)


def default_live_validation_product_priority() -> Tuple[str, ...]:
    """
    Ordered list of spot products to try for Avenue A / Gate A micro-validation.

    Override with env ``LIVE_VALIDATION_PRODUCT_PRIORITY``:

    - Comma-separated, e.g. ``BTC-USD,BTC-USDC``
    - Or a JSON array string, e.g. ``["BTC-USD","BTC-USDC"]``

    Each candidate must still pass runtime and venue checks.
    """
    raw = (os.environ.get("LIVE_VALIDATION_PRODUCT_PRIORITY") or "").strip()
    if raw:
        parts: List[str] = []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    parts = [str(p).strip().upper() for p in parsed if str(p).strip()]
            except json.JSONDecodeError:
                parts = []
        if not parts and raw:
            parts = [p.strip().upper() for p in re.split(r"[,\s]+", raw) if p.strip()]
        if parts:
            return tuple(dict.fromkeys(parts))  # preserve order, dedupe
    return _default_nte_coinbase_products()


def ordered_validation_candidates() -> List[str]:
    """Priority list intersected with uniqueness (first wins)."""
    return list(dict.fromkeys(default_live_validation_product_priority()))
