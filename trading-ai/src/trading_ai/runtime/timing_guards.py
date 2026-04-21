"""Timing anomalies: buy fill delay, hold cap, total trade duration."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _rel() -> str:
    return "data/control/timing_anomalies.json"


def _defaults() -> Dict[str, Any]:
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "max_buy_fill_seconds": float(os.environ.get("EZRAS_MAX_BUY_FILL_SEC") or "120"),
        "max_hold_seconds": float(os.environ.get("EZRAS_MAX_HOLD_SEC") or "86400"),
        "max_trade_duration_seconds": float(os.environ.get("EZRAS_MAX_TRADE_DURATION_SEC") or "172800"),
        "anomalies": [],
    }


def load_timing_config(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    raw = ad.read_json(_rel())
    base = _defaults()
    if raw:
        base.update({k: v for k, v in raw.items() if k != "anomalies"})
    return base


def record_timing_anomaly(
    event: Dict[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> None:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    cur = ad.read_json(_rel()) or _defaults()
    anom = list(cur.get("anomalies") or [])
    row = dict(event)
    row["ts"] = time.time()
    row["iso"] = datetime.now(timezone.utc).isoformat()
    anom.append(row)
    cur["anomalies"] = anom[-500:]
    cur["updated_at"] = datetime.now(timezone.utc).isoformat()
    ad.write_json(_rel(), cur)


def check_buy_fill_elapsed(
    *,
    order_placed_ts: float,
    fill_ts: Optional[float],
    trade_id: str,
    runtime_root: Optional[Path] = None,
) -> bool:
    cfg = load_timing_config(runtime_root=runtime_root)
    max_sec = float(cfg.get("max_buy_fill_seconds") or 120)
    if fill_ts is None:
        return True
    dt = float(fill_ts) - float(order_placed_ts)
    if dt > max_sec:
        record_timing_anomaly(
            {
                "kind": "buy_fill_slow",
                "trade_id": trade_id,
                "elapsed_sec": dt,
                "limit_sec": max_sec,
            },
            runtime_root=runtime_root,
        )
        return False
    return True


def check_hold_exceeded(
    *,
    entry_ts: float,
    exit_ts: float,
    trade_id: str,
    runtime_root: Optional[Path] = None,
) -> bool:
    cfg = load_timing_config(runtime_root=runtime_root)
    max_hold = float(cfg.get("max_hold_seconds") or 86400)
    dt = float(exit_ts) - float(entry_ts)
    if dt > max_hold:
        record_timing_anomaly(
            {
                "kind": "hold_exceeded",
                "trade_id": trade_id,
                "elapsed_sec": dt,
                "limit_sec": max_hold,
            },
            runtime_root=runtime_root,
        )
        return False
    return True
