"""
Coinbase Advanced Trade scalp engine: 20s scanner + 5s position manager + optional market WebSocket.

Run standalone::

    python -m trading_ai.shark.coinbase_scalp_engine

Or instantiate :class:`CoinbaseScalpEngine` from another scheduler.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.shark.coinbase_scalp_config import CoinbaseScalpConfig, coinbase_scalp_enabled
from trading_ai.shark.coinbase_scalp_position_manager import (
    CoinbaseScalpPositionManager,
    reset_daily_if_needed,
)
from trading_ai.shark.coinbase_scalp_scanner import CoinbaseScalpScanner
from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.outlets.coinbase import CoinbaseAuthError, CoinbaseClient

load_shark_dotenv()
logger = logging.getLogger(__name__)

_DEFAULT_STATE: Dict[str, Any] = {
    "version": 1,
    "positions": [],
    "daily_pnl_usd": 0.0,
    "daily_pnl_date": "",
    "consecutive_losses": 0,
}


def _state_path() -> Path:
    return shark_state_path("coinbase_scalp.json")


def load_scalp_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        st = dict(_DEFAULT_STATE)
        reset_daily_if_needed(st)
        return st
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            st = dict(_DEFAULT_STATE)
            st.update(raw)
            reset_daily_if_needed(st)
            return st
    except Exception as exc:
        logger.warning("coinbase_scalp.json load error: %s — using defaults", exc)
    st = dict(_DEFAULT_STATE)
    reset_daily_if_needed(st)
    return st


def save_scalp_state(state: Dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("coinbase_scalp.json save error: %s", exc)


class CoinbaseScalpEngine:
    """
    Orchestrates scanner interval (default 20s), position checks (5s), and optional WS price cache.
    """

    def __init__(
        self,
        config: Optional[CoinbaseScalpConfig] = None,
        client: Optional[CoinbaseClient] = None,
    ) -> None:
        self._cfg = config or CoinbaseScalpConfig.from_env()
        self._client = client or CoinbaseClient()
        self._price_lock = threading.Lock()
        self._price_cache: Dict[str, Tuple[float, float, float]] = {}
        self._history: Dict[str, Any] = {}  # product_id -> deque of (ts, mid); filled by scanner
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        self._scanner = CoinbaseScalpScanner(
            self._client, self._cfg, price_history=self._history
        )
        self._pm = CoinbaseScalpPositionManager(
            self._client,
            self._cfg,
            get_price_cache=self._get_price_cache,
        )

    def _get_price_cache(self) -> Dict[str, Tuple[float, float, float]]:
        with self._price_lock:
            return dict(self._price_cache)

    def _update_cache_rest(self) -> None:
        try:
            px = self._client.get_prices(list(self._cfg.products))
        except Exception as exc:
            logger.debug("scalp REST price refresh: %s", exc)
            return
        now = time.time()
        with self._price_lock:
            for pid, (bid, ask) in px.items():
                if bid > 0 or ask > 0:
                    self._price_cache[pid] = (bid, ask, now)

    def _run_market_websocket(self) -> None:
        try:
            import websocket  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "websocket-client not installed — scalp uses REST for prices only "
                "(pip install websocket-client)"
            )
            return

        products = list(self._cfg.products)
        url = self._cfg.ws_url

        def on_message(_ws: Any, message: str) -> None:
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                return
            now = time.time()
            events = payload.get("events") or []
            if not isinstance(events, list):
                return
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                tickers = ev.get("tickers") or ev.get("ticker") or []
                if isinstance(tickers, dict):
                    tickers = [tickers]
                if not isinstance(tickers, list):
                    continue
                for t in tickers:
                    if not isinstance(t, dict):
                        continue
                    pid = str(t.get("product_id") or "")
                    if pid not in products:
                        continue
                    try:
                        bid = float(t.get("best_bid") or t.get("bid") or 0.0)
                        ask = float(t.get("best_ask") or t.get("ask") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if bid <= 0 and ask <= 0:
                        p = t.get("price")
                        try:
                            mp = float(p) if p is not None else 0.0
                        except (TypeError, ValueError):
                            mp = 0.0
                        if mp > 0:
                            bid = mp
                            ask = mp
                    if bid > 0 or ask > 0:
                        with self._price_lock:
                            self._price_cache[pid] = (bid, ask, now)

        def on_open(ws: Any) -> None:
            sub_ticker = {
                "type": "subscribe",
                "product_ids": products,
                "channel": "ticker",
            }
            ws.send(json.dumps(sub_ticker))
            hb = {
                "type": "subscribe",
                "product_ids": products,
                "channel": "heartbeats",
            }
            try:
                ws.send(json.dumps(hb))
            except Exception:
                pass

        def loop() -> None:
            backoff = 1.0
            while not self._stop.is_set():
                try:
                    ws = websocket.WebSocketApp(
                        url,
                        on_message=on_message,
                        on_open=on_open,
                    )
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as exc:
                    logger.warning("scalp WS error: %s — reconnect in %.1fs", exc, backoff)
                if self._stop.wait(min(60.0, backoff)):
                    break
                backoff = min(60.0, backoff * 1.5)

        t = threading.Thread(target=loop, name="coinbase_scalp_ws", daemon=True)
        t.start()
        self._threads.append(t)

    def _scanner_loop(self) -> None:
        interval = max(5.0, float(self._cfg.scanner_interval_seconds))
        while not self._stop.is_set():
            if self._client.has_credentials():
                try:
                    state = load_scalp_state()
                    self._scanner.scan_and_maybe_enter(state)
                    save_scalp_state(state)
                except CoinbaseAuthError as exc:
                    logger.error("scalp scanner auth: %s", exc)
                except Exception as exc:
                    logger.warning("scalp scanner: %s", exc)
            if self._stop.wait(interval):
                break

    def _position_loop(self) -> None:
        interval = max(1.0, float(self._cfg.position_check_interval_seconds))
        while not self._stop.is_set():
            if self._client.has_credentials():
                try:
                    self._update_cache_rest()
                    state = load_scalp_state()
                    self._pm.check_positions(state)
                    save_scalp_state(state)
                except CoinbaseAuthError as exc:
                    logger.error("scalp position manager auth: %s", exc)
                except Exception as exc:
                    logger.warning("scalp position loop: %s", exc)
            if self._stop.wait(interval):
                break

    def start_background(self) -> None:
        """Start scanner + position threads (non-blocking)."""
        if self._cfg.enable_market_websocket:
            self._run_market_websocket()

        t1 = threading.Thread(target=self._scanner_loop, name="coinbase_scalp_scan", daemon=True)
        t2 = threading.Thread(target=self._position_loop, name="coinbase_scalp_pm", daemon=True)
        t1.start()
        t2.start()
        self._threads.extend([t1, t2])

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)

    def run_one_tick(self) -> Dict[str, Any]:
        """Single combined tick (for tests / manual invocation): refresh prices, exits, scan."""
        out: Dict[str, Any] = {"ok": True}
        if not self._client.has_credentials():
            out["ok"] = False
            out["error"] = "no_credentials"
            return out
        self._update_cache_rest()
        state = load_scalp_state()
        self._pm.check_positions(state)
        scan = self._scanner.scan_and_maybe_enter(state)
        save_scalp_state(state)
        out["scan"] = scan
        return out


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not coinbase_scalp_enabled():
        logger.error("Set COINBASE_SCALP_ENABLED=1 to run the scalp engine.")
        raise SystemExit(1)
    eng = CoinbaseScalpEngine()
    if not eng._client.has_credentials():
        logger.error("Set COINBASE_API_KEY_NAME and COINBASE_API_PRIVATE_KEY for Advanced Trade.")
        raise SystemExit(1)
    eng.start_background()
    logger.info(
        "Coinbase scalp engine running (scan=%ss, position=%ss). Ctrl+C to stop.",
        eng._cfg.scanner_interval_seconds,
        eng._cfg.position_check_interval_seconds,
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        eng.stop()


if __name__ == "__main__":
    main()
