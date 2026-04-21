"""Quote freshness + bid/ask sanity for Gate B."""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional


def evaluate_data_quality(
    *,
    quote_ts: Optional[float] = None,
    max_age_sec: float = 8.0,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
) -> Dict[str, Any]:
    reasons: List[str] = []
    now = time.time()
    ts = float(quote_ts or now)
    age = now - ts
    acceptable = True
    if age > max_age_sec:
        acceptable = False
        reasons.append("stale_quote")
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        acceptable = False
        reasons.append("inconsistent_bid_ask")
    elif bid > ask:
        acceptable = False
        reasons.append("inconsistent_bid_ask")
    spread = (ask - bid) / ask if ask else float("nan")
    if not math.isnan(spread) and spread < 0:
        acceptable = False
        reasons.append("inconsistent_bid_ask")
    dq_score = 1.0 if acceptable else 0.2
    return {
        "acceptable": acceptable,
        "data_quality_score": dq_score,
        "reject_reasons": reasons,
        "field_provenance": {
            "quote_ts": "caller_supplied_hint",
            "best_bid": "caller_supplied_hint",
            "best_ask": "caller_supplied_hint",
        },
    }
