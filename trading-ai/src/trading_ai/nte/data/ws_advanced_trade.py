"""
Coinbase Advanced Trade WebSocket — ticker + heartbeats for BTC-USD / ETH-USD (public).

Uses ``websocket-client``. Subscribe on ``on_open`` (ticker then heartbeats within a few
seconds); reconnect with backoff. Heartbeats keep sparse channels alive per CDP guidance.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_ADV_WS = "wss://advanced-trade-ws.coinbase.com"


def _parse_ticker_payload(data: dict, out: Dict[str, dict]) -> None:
    ch = str(data.get("channel") or data.get("type") or "").lower()
    events = data.get("events") or []
    if isinstance(data.get("tickers"), list):
        events = data["tickers"]
    if not isinstance(events, list):
        events = [data] if data.get("product_id") else []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        pid = str(ev.get("product_id") or ev.get("productId") or "").strip()
        if not pid:
            continue
        row = out.setdefault(pid, {})
        for k in ("best_bid", "best_bid_price", "bid"):
            v = ev.get(k)
            if v is not None:
                try:
                    row["best_bid"] = float(v)
                except (TypeError, ValueError):
                    pass
                break
        for k in ("best_ask", "best_ask_price", "ask"):
            v = ev.get(k)
            if v is not None:
                try:
                    row["best_ask"] = float(v)
                except (TypeError, ValueError):
                    pass
                break
        for k in ("price", "last", "trade_price"):
            v = ev.get(k)
            if v is not None:
                try:
                    row["last_trade"] = float(v)
                except (TypeError, ValueError):
                    pass
                break
        if ch:
            row["_channel"] = ch


def _is_public_heartbeat(data: dict) -> bool:
    ch = str(data.get("channel") or "").lower()
    if ch == "heartbeats":
        return True
    if str(data.get("type") or "").lower() == "heartbeat":
        return True
    for ev in data.get("events") or []:
        if isinstance(ev, dict) and str(ev.get("type") or "").lower() == "heartbeat":
            return True
    return False


class AdvancedTradeWSFeed:
    """Background thread: shared ``latest[product_id]`` = {best_bid, best_ask, last_trade}."""

    def __init__(
        self,
        product_ids: List[str],
        on_update: Optional[Callable[[], None]] = None,
    ) -> None:
        self.product_ids = list(dict.fromkeys(product_ids))
        self._on_update = on_update
        self._latest: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._app = None
        self._last_tick_ts: float = 0.0
        self._last_heartbeat_ts: float = 0.0

    def latest(self) -> Dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._latest.items()}

    def _activity_ts(self) -> float:
        with self._lock:
            a = self._last_tick_ts
            b = self._last_heartbeat_ts
        if a <= 0 and b <= 0:
            return 0.0
        if a <= 0:
            return b
        if b <= 0:
            return a
        return max(a, b)

    def last_tick_age_sec(self) -> float:
        """Age since last parsed ticker payload (inf if never)."""
        with self._lock:
            ts = self._last_tick_ts
        if ts <= 0:
            return float("inf")
        return max(0.0, time.time() - ts)

    def last_feed_activity_age_sec(self) -> float:
        """Age since last ticker **or** heartbeat (for liveness when quotes are sparse)."""
        ref = self._activity_ts()
        if ref <= 0:
            return float("inf")
        return max(0.0, time.time() - ref)

    def is_stale(self, *, max_age_sec: float = 90.0) -> bool:
        return self.last_feed_activity_age_sec() > max_age_sec

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="nte_cb_ws", daemon=True)
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
            logger.warning("websocket-client not installed — WS feed disabled")
            return

        backoff = 1.0
        while not self._stop.is_set():
            try:
                connect_started = time.time()

                def on_open(ws: object) -> None:
                    t0 = time.time()
                    try:
                        ws.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "product_ids": self.product_ids,
                                    "channel": "ticker",
                                }
                            )
                        )
                        ws.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "product_ids": self.product_ids,
                                    "channel": "heartbeats",
                                }
                            )
                        )
                        logger.info(
                            "Advanced Trade WS subscribed ticker+heartbeats %s send_ms=%.1f",
                            self.product_ids,
                            (time.time() - t0) * 1000.0,
                        )
                    except Exception as exc:
                        logger.warning("CB WS subscribe send failed: %s", exc)

                def on_message(_ws: object, message: str) -> None:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        return
                    if not isinstance(data, dict):
                        return
                    if data.get("type") == "error":
                        logger.warning("CB WS error: %s", str(data)[:400])
                        return
                    if _is_public_heartbeat(data):
                        with self._lock:
                            self._last_heartbeat_ts = time.time()
                        return
                    scratch: Dict[str, dict] = {}
                    _parse_ticker_payload(data, scratch)
                    if not scratch and "events" in data:
                        for ev in data.get("events") or []:
                            if isinstance(ev, dict):
                                _parse_ticker_payload(
                                    {"events": [ev], "channel": data.get("channel")},
                                    scratch,
                                )
                    if not scratch:
                        return
                    with self._lock:
                        self._last_tick_ts = time.time()
                        for pid, row in scratch.items():
                            self._latest.setdefault(pid, {}).update(row)
                    if self._on_update:
                        try:
                            self._on_update()
                        except Exception:
                            pass

                self._app = websocket.WebSocketApp(
                    _ADV_WS,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=lambda _ws, err: logger.debug("CB WS: %s", err),
                )
                self._app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                if self._stop.is_set():
                    break
                logger.warning("Advanced Trade WS stopped (%s); reconnect in %.1fs", exc, backoff)
                time.sleep(min(backoff, 60.0))
                backoff = min(backoff * 2, 60.0)
            else:
                backoff = 1.0
