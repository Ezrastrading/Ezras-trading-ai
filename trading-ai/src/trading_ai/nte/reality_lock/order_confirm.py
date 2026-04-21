"""Order confirmation loop: no position updates until fill is confirmed."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


class OrderStatusClient(Protocol):
    def get_order(self, order_id: str) -> Dict[str, Any]: ...

    def get_fills(self, order_id: str) -> List[Dict[str, Any]]: ...

    def cancel_order(self, order_id: str) -> bool: ...


def _normalize_status(raw: str) -> str:
    s = (raw or "").strip().upper()
    if "PARTIALLY" in s or s == "OPEN":
        return "OPEN"
    if "FILLED" in s or s in ("DONE", "FILLED"):
        return "FILLED"
    if "CANCEL" in s or s == "EXPIRED":
        return "CANCELLED"
    if "FAIL" in s or s == "REJECTED":
        return "FAILED"
    return s


def wait_for_fill(
    client: OrderStatusClient,
    order_id: str,
    *,
    max_wait_sec: float,
    poll_sec: float = 0.2,
    max_stale_order_age_sec: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Poll until FILLED, or fail on terminal bad state / timeout (cancel + ORDER_TIMEOUT_ABORT).

    Returns raw fills list from the venue for FILLED state.
    """
    start = time.time()
    oid = str(order_id or "").strip()
    if not oid:
        raise Exception("ORDER_FAILED")

    while time.time() - start < max_wait_sec:
        try:
            order = client.get_order(oid)
        except Exception as exc:
            logger.warning("wait_for_fill get_order %s: %s", oid[:16], exc)
            time.sleep(poll_sec)
            continue
        if not isinstance(order, dict):
            time.sleep(poll_sec)
            continue
        if max_stale_order_age_sec is not None:
            from trading_ai.nte.reality_lock.market_reality import check_order_timestamp_fresh

            if not check_order_timestamp_fresh(order, max_age_sec=float(max_stale_order_age_sec)):
                raise Exception("STALE_ORDER_TIMESTAMP")
        st = _normalize_status(
            str(order.get("status") or order.get("order_status") or "")
        )
        fsz = 0.0
        tot = 0.0
        try:
            fsz = float(order.get("filled_size") or order.get("filled_quantity") or 0)
            tot = float(order.get("base_size") or order.get("order_total_size") or 0)
        except (TypeError, ValueError):
            pass
        filled_by_size = tot > 0 and fsz >= tot * 0.999
        if st == "FILLED" or filled_by_size:
            return client.get_fills(oid)
        if st == "CANCELLED":
            raise Exception("ORDER_FAILED")
        if st == "FAILED":
            raise Exception("ORDER_FAILED")
        time.sleep(poll_sec)

    try:
        client.cancel_order(oid)
    except Exception as exc:
        logger.warning("wait_for_fill cancel failed %s: %s", oid[:16], exc)
    raise Exception("ORDER_TIMEOUT_ABORT")
