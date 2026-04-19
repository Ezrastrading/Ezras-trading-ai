#!/usr/bin/env python3
"""
Optional live probe: Coinbase Advanced Trade public ticker+heartbeats and (if creds)
authenticated user stream. Requires websocket-client and valid API key material for user leg.

Usage (from repo root, with env loaded):

  python scripts/coinbase_ws_live_probe.py

Exit 0 if public socket receives a message within timeout; user leg is best-effort.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

TIMEOUT_SEC = 25.0


def _public_probe() -> bool:
    try:
        import websocket
    except ImportError:
        print("websocket-client not installed", file=sys.stderr)
        return False

    ok = threading.Event()
    err: list[str] = []

    def on_message(_ws: object, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict):
            ok.set()

    def on_open(ws: object) -> None:
        try:
            ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channel": "ticker",
                    }
                )
            )
            ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channel": "heartbeats",
                    }
                )
            )
        except Exception as e:
            err.append(str(e))

    app = websocket.WebSocketApp(
        "wss://advanced-trade-ws.coinbase.com",
        on_open=on_open,
        on_message=on_message,
        on_error=lambda _w, e: err.append(str(e)),
    )
    t = threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10))
    t.daemon = True
    t.start()
    if not ok.wait(timeout=TIMEOUT_SEC):
        try:
            app.close()
        except Exception:
            pass
        print("public WS: no message within timeout", file=sys.stderr)
        if err:
            print("errors:", err, file=sys.stderr)
        return False
    try:
        app.close()
    except Exception:
        pass
    print("public WS: PASS (received frame)")
    return True


def _user_probe() -> None:
    key = (os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY") or "").strip()
    if not key:
        print("user WS: SKIP (no COINBASE_API_KEY_NAME)")
        return
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient
    except Exception as e:
        print(f"user WS: SKIP import CoinbaseClient ({e})")
        return
    try:
        import websocket
    except ImportError:
        print("user WS: SKIP (no websocket-client)")
        return

    client = CoinbaseClient()
    ok = threading.Event()

    def on_message(_ws: object, message: str) -> None:
        ok.set()

    def on_open(ws: object) -> None:
        tok = client.build_user_stream_jwt()
        ws.send(json.dumps({"type": "subscribe", "channel": "heartbeats", "jwt": tok}))
        tok2 = client.build_user_stream_jwt()
        ws.send(
            json.dumps(
                {
                    "type": "subscribe",
                    "channel": "user",
                    "jwt": tok2,
                    "product_ids": ["BTC-USD"],
                }
            )
        )

    app = websocket.WebSocketApp(
        "wss://advanced-trade-ws-user.coinbase.com",
        on_open=on_open,
        on_message=on_message,
        on_error=lambda _w, e: print("user WS error:", e, file=sys.stderr),
    )
    t = threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10))
    t.daemon = True
    t.start()
    if ok.wait(timeout=TIMEOUT_SEC):
        print("user WS: PASS (received frame)")
    else:
        print("user WS: FAIL or timeout — try NTE_COINBASE_USER_WS_JWT_MODE=legacy_uri", file=sys.stderr)
    try:
        app.close()
    except Exception:
        pass


def main() -> int:
    pub = _public_probe()
    _user_probe()
    return 0 if pub else 1


if __name__ == "__main__":
    raise SystemExit(main())
