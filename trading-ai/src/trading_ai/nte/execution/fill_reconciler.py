"""Reconcile user-stream events with REST order/fill polling."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class FillReconciler:
    """
    Merge **user-stream–primary** lifecycle with REST snapshots.

    REST is **fallback** when the user WebSocket is stale or missing — never the
    default source of truth when the stream is healthy.
    """

    def __init__(
        self,
        *,
        get_order: Callable[[str], Dict[str, Any]],
        get_fills: Callable[[str], List[Dict[str, Any]]],
        stream_stale_check: Callable[[], bool],
    ) -> None:
        self._get_order = get_order
        self._get_fills = get_fills
        self._stream_stale_check = stream_stale_check

    def reconcile_order(self, order_id: str) -> Dict[str, Any]:
        use_poll = self._stream_stale_check()
        if use_poll:
            logger.info("fill reconciler: using REST (user stream stale) order_id=%s", order_id)
        order = self._get_order(order_id)
        fills = self._get_fills(order_id)
        return {
            "ts": time.time(),
            "primary_truth": "polling_fallback" if use_poll else "user_stream",
            "source": "polling" if use_poll else "ws_primary",  # backward compat
            "order": order,
            "fills": fills,
        }
