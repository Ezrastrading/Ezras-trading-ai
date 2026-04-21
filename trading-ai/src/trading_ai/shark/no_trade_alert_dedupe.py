"""no_trade 10m Telegram dedupe — one alert per trade-ref streak, persisted, concurrent-safe."""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import Callable


_MIN_IDLE_SEC = 600.0


def try_send_no_trade_idle_alert(
    *,
    now: float,
    trade_ref_epoch: float,
    send: Callable[[str], bool],
    state_path: Path,
) -> bool:
    if (now - float(trade_ref_epoch)) < _MIN_IDLE_SEC:
        return False
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    dedupe_key = f"no_trade_10m:global:{round(float(trade_ref_epoch), 6)}"
    with state_path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            raw = fh.read()
            data: dict = {}
            if raw.strip():
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}
            if float(data.get("announced_for_trade_ref_epoch") or -1.0) == float(trade_ref_epoch):
                return False
            msg = f"no_trade_idle>{_MIN_IDLE_SEC}s trade_ref={trade_ref_epoch}"
            if not send(msg):
                return False
            out = {
                "schema_version": 1,
                "announced_for_trade_ref_epoch": float(trade_ref_epoch),
                "last_alert_sent_unix": float(now),
                "last_alert_dedupe_key": dedupe_key,
            }
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(out) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return True
