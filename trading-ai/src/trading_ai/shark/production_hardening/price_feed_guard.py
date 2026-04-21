"""Price freshness guard."""

from __future__ import annotations

import os
import time
from typing import Tuple


def check_price_sanity(*, product_key: str, price: float, price_ts_unix: float) -> Tuple[bool, str]:
    _ = product_key, price
    if os.environ.get("PRODUCTION_HARDENING_LAYER", "0") not in ("1", "true", "yes"):
        return True, "layer_off"
    max_stale = float(os.environ.get("MAX_STALE_PRICE_MS", "5000")) / 1000.0
    age = time.time() - float(price_ts_unix)
    if age > max_stale:
        return False, f"stale_price_age_sec={age:.3f}"
    return True, "ok"
