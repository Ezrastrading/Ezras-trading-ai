"""
Authenticated Coinbase Advanced Trade user WebSocket — orders, fills, lifecycle.

Endpoint: ``wss://advanced-trade-ws-user.coinbase.com`` with JWT per CDP.
Subscribe to ``user`` and ``heartbeats`` within seconds of connect (server disconnects
otherwise). **User stream is primary truth**; REST polling is only for reconciliation
when this feed is stale or missing.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_USER_WS_URL = "wss://advanced-trade-ws-user.coinbase.com"


def normalize_user_channel_message(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Turn one Advanced Trade ``user`` channel JSON object into normalized lifecycle rows.

    Each row includes: ``kind`` (order_update), ``order_id``, ``product_id``, ``side``,
    ``status`` (upper), ``filled_base``, ``remaining_base`` (best-effort), ``raw_slice``.
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return out
    ch = str(payload.get("channel") or "").lower()
    if ch and ch not in ("user", "subscriptions", "heartbeats", ""):
        pass
    events = payload.get("events")
    if not isinstance(events, list):
        events = [payload] if payload.get("order_id") or payload.get("orders") else []

    for ev in events:
        if not isinstance(ev, dict):
            continue
        orders = ev.get("orders")
        if isinstance(orders, list):
            for od in orders:
                if isinstance(od, dict):
                    out.append(_normalize_order_dict(od, ev))
        else:
            od = ev.get("order") or ev
            if isinstance(od, dict) and (od.get("order_id") or od.get("order_id_str")):
                out.append(_normalize_order_dict(od, ev))
            elif ev.get("type") == "snapshot" and isinstance(ev.get("orders"), list):
                for od in ev["orders"]:
                    if isinstance(od, dict):
                        out.append(_normalize_order_dict(od, ev))
    return out


def _f(x: Any) -> float:
    try:
        if x is None or x == "":
            return 0.0
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _normalize_order_dict(od: Dict[str, Any], parent: Dict[str, Any]) -> Dict[str, Any]:
    oid = str(od.get("order_id") or od.get("order_id_str") or od.get("id") or "")
    pid = str(od.get("product_id") or od.get("product_id_str") or "")
    side = str(od.get("side") or "").upper()
    status = str(
        od.get("status") or od.get("order_status") or od.get("lifecycle") or ""
    ).upper()
    filled = _f(
        od.get("filled_size")
        or od.get("filled_quantity")
        or od.get("filled_value")
        or od.get("cum_qty")
    )
    total = _f(od.get("base_size") or od.get("size") or od.get("order_total_size"))
    remaining = _f(od.get("remaining_size"))
    if total > 0 and remaining == 0 and filled > 0:
        remaining = max(0.0, total - filled)
    elif total > 0 and remaining == 0:
        remaining = max(0.0, total - filled)

    su = status.lower()
    lifecycle = "open"
    if "cancel" in su:
        lifecycle = "canceled"
    elif "reject" in su:
        lifecycle = "rejected"
    elif "expir" in su:
        lifecycle = "expired"
    elif filled > 1e-12 and remaining > 1e-12:
        lifecycle = "partially_filled"
    elif filled > 1e-12 and remaining <= 1e-12:
        lifecycle = "fully_filled"
    elif su in ("open", "pending", "active", "queued", "accepted", "unknown", ""):
        lifecycle = "open" if filled <= 1e-12 else "partially_filled"

    exit_hint = side == "SELL" and lifecycle == "fully_filled"

    return {
        "kind": "order_update",
        "order_id": oid,
        "product_id": pid,
        "side": side,
        "status": status or lifecycle,
        "lifecycle": lifecycle,
        "filled_base": filled,
        "remaining_base": remaining,
        "total_base": total,
        "exit_filled_confirmed": exit_hint,
        "parent_event": {k: parent.get(k) for k in ("type", "timestamp") if k in parent},
        "raw_slice": od,
    }


def _is_heartbeat_frame(data: Dict[str, Any]) -> bool:
    ch = str(data.get("channel") or "").lower()
    if ch == "heartbeats":
        return True
    if str(data.get("type") or "").lower() == "heartbeat":
        return True
    for ev in data.get("events") or []:
        if isinstance(ev, dict) and str(ev.get("type") or "").lower() == "heartbeat":
            return True
    return False


class CoinbaseUserStreamFeed:
    """
    Background thread: authenticated user stream, normalized callbacks.

    Pass ``jwt_factory`` (typically :meth:`CoinbaseClient.build_user_stream_jwt`) or
    ``coinbase_client``; otherwise the feed cannot connect.

    Each subscribe uses a **fresh** JWT (CDP may require a new token per message).
    """

    def __init__(
        self,
        product_ids: Sequence[str],
        *,
        on_event: Callable[[Dict[str, Any]], None],
        jwt_factory: Optional[Callable[[], str]] = None,
        coinbase_client: Any = None,
        ws_url: str = _USER_WS_URL,
        stale_sec: float = 120.0,
    ) -> None:
        self.product_ids = list(dict.fromkeys(product_ids))
        self._on_event = on_event
        self._jwt_factory = jwt_factory
        self._client = coinbase_client
        self._ws_url = ws_url
        self._stale_sec = stale_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._app: Any = None
        self._last_msg_ts: float = 0.0
        self._last_heartbeat_ts: float = 0.0
        self._last_order_event_ts: float = 0.0
        self._lock = threading.Lock()

    def _reference_ts(self) -> float:
        """Most recent user-relevant activity (heartbeat or any frame)."""
        with self._lock:
            hb = self._last_heartbeat_ts
            msg = self._last_msg_ts
        if hb <= 0 and msg <= 0:
            return 0.0
        if hb <= 0:
            return msg
        if msg <= 0:
            return hb
        return max(hb, msg)

    def last_message_age_sec(self) -> float:
        ref = self._reference_ts()
        if ref <= 0:
            return float("inf")
        return max(0.0, time.time() - ref)

    def is_stale(self) -> bool:
        return self.last_message_age_sec() > self._stale_sec

    def touch_activity(self) -> None:
        with self._lock:
            self._last_msg_ts = time.time()

    def _touch_heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat_ts = time.time()
            self._last_msg_ts = max(self._last_msg_ts, self._last_heartbeat_ts)

    def _touch_order_activity(self) -> None:
        now = time.time()
        with self._lock:
            self._last_order_event_ts = now
            self._last_msg_ts = max(self._last_msg_ts, now)

    def last_order_event_age_sec(self) -> float:
        with self._lock:
            ts = self._last_order_event_ts
        if ts <= 0:
            return float("inf")
        return max(0.0, time.time() - ts)

    def _get_jwt(self) -> str:
        if self._jwt_factory:
            return self._jwt_factory()
        if self._client is not None:
            return self._client.build_user_stream_jwt()
        raise RuntimeError("Coinbase user stream: need jwt_factory or coinbase_client")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="cb-user-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._app is not None:
                self._app.close()
        except Exception:
            pass

    def _run_loop(self) -> None:
        try:
            import websocket
        except ImportError:
            logger.warning("websocket-client not installed — user stream disabled")
            return

        backoff = 1.0
        while not self._stop.is_set():
            try:
                connect_started = time.time()

                def on_open(ws: Any) -> None:
                    open_lag_ms = (time.time() - connect_started) * 1000.0
                    try:
                        tok_hb = self._get_jwt()
                    except Exception as exc:
                        logger.error("user stream JWT failed: %s", exc)
                        return
                    sub_started = time.time()
                    try:
                        ws.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "channel": "heartbeats",
                                    "jwt": tok_hb,
                                }
                            )
                        )
                        tok_user = self._get_jwt()
                        sub_user = {
                            "type": "subscribe",
                            "channel": "user",
                            "jwt": tok_user,
                            "product_ids": self.product_ids,
                        }
                        ws.send(json.dumps(sub_user))
                        sub_ms = (time.time() - sub_started) * 1000.0
                        logger.info(
                            "Coinbase user WS subscribed heartbeats+user products=%s "
                            "open_lag_ms=%.1f subscribe_send_ms=%.1f (send within 5s required)",
                            self.product_ids,
                            open_lag_ms,
                            sub_ms,
                        )
                    except Exception as exc:
                        logger.warning("user stream subscribe failed: %s", exc)

                def on_message(_ws: Any, message: str) -> None:
                    self.touch_activity()
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        return
                    if not isinstance(data, dict):
                        return
                    if _is_heartbeat_frame(data):
                        self._touch_heartbeat()
                    if data.get("type") == "error":
                        logger.warning("Coinbase user WS error frame: %s", str(data)[:500])
                        self._on_event(
                            {
                                "kind": "stream_error",
                                "raw": data,
                                "ts": time.time(),
                            }
                        )
                        return
                    rows = normalize_user_channel_message(data)
                    for row in rows:
                        self._touch_order_activity()
                        try:
                            self._on_event(row)
                        except Exception as exc:
                            logger.warning("user stream on_event failed: %s", exc)
                    if not rows and not _is_heartbeat_frame(data):
                        try:
                            self._on_event(
                                {
                                    "kind": "raw_user_message",
                                    "raw": data,
                                    "ts": time.time(),
                                }
                            )
                        except Exception:
                            pass

                def on_pong(_ws: Any, _data: str) -> None:
                    self.touch_activity()

                self._app = websocket.WebSocketApp(
                    self._ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=lambda _ws, err: logger.debug("CB user WS: %s", err),
                    on_pong=on_pong,
                )
                self._app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                if self._stop.is_set():
                    break
                logger.warning("Coinbase user WS stopped (%s); reconnect in %.1fs", exc, backoff)
                time.sleep(min(backoff, 60.0))
                backoff = min(backoff * 2, 60.0)
            else:
                backoff = 1.0

    @staticmethod
    def parse_order_event(raw: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return data
