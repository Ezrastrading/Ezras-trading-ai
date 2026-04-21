"""
Execution cooldown — limit re-entry frequency after a successful trade.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 60.0
_STATE_NAME = "intelligence_cooldown.json"


def _path() -> Path:
    return shark_state_path(_STATE_NAME)


def _load() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"last_trade_unix": 0.0}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("cooldown state load failed: %s", exc)
    return {"last_trade_unix": 0.0}


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def last_trade_timestamp() -> float:
    return float(_load().get("last_trade_unix") or 0.0)


def cooldown_active(now: Optional[float] = None) -> bool:
    now = float(now if now is not None else time.time())
    last = last_trade_timestamp()
    if last <= 0:
        return False
    active = (now - last) < COOLDOWN_SECONDS
    if active:
        logger.info(
            "cooldown_engine: BLOCK remaining_sec=%.2f last_trade_unix=%.3f",
            COOLDOWN_SECONDS - (now - last),
            last,
        )
    return active


def record_successful_execution(now: Optional[float] = None) -> None:
    """Call only after a trade is successfully executed (filled / confirmed)."""
    ts = float(now if now is not None else time.time())
    _save({"last_trade_unix": ts})
    logger.info("cooldown_engine: last_trade_timestamp updated to %.3f", ts)
