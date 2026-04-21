"""Recent order id tracking (TTL) — ignore duplicate submissions."""

from __future__ import annotations

import time
from typing import Set


class RecentOrderIdTracker:
    """In-memory set with TTL seconds — duplicate order_id within window → ignore."""

    def __init__(self, *, ttl_sec: float = 3600.0) -> None:
        self._ttl_sec = float(ttl_sec)
        self._seen: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl_sec
        dead = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in dead:
            del self._seen[k]

    def is_duplicate(self, order_id: str) -> bool:
        oid = str(order_id or "").strip()
        if not oid:
            return False
        now = time.time()
        self._prune(now)
        if oid in self._seen:
            return True
        self._seen[oid] = now
        return False


_recent_orders = RecentOrderIdTracker()


def global_order_tracker() -> RecentOrderIdTracker:
    return _recent_orders
