"""Classify failures, append to failure log, optional degrade/pause hints."""

from __future__ import annotations

import json
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.paths import nte_failure_log_path
from trading_ai.nte.utils.atomic_json import atomic_write_json

logger = logging.getLogger(__name__)


class FailureClass(str, Enum):
    WS_DISCONNECT = "websocket_disconnect"
    STALE_DATA = "stale_market_data"
    ORDER_FAILED = "order_placement_failed"
    CANCEL_FAILED = "order_cancel_failed"
    PARTIAL_FILL = "partial_fill_mismatch"
    BALANCE_MISMATCH = "balance_mismatch"
    MEMORY_CORRUPT = "memory_file_corruption"
    DUPLICATE_LOG = "duplicate_trade_log"
    ROUTE_LOOP = "route_loop"
    AVENUE_CONTAMINATION = "avenue_contamination"
    SANDBOX_PROMOTION = "sandbox_promotion_attempt"
    CEO_NO_OUTPUT = "ceo_session_no_output"
    REWARD_DRIFT = "reward_engine_drift"
    GOAL_STALE = "goal_engine_stale"
    CONFIG_INVALID = "config_invalid"
    CLOCK_SKEW = "clock_timestamp"
    MODE_MISMATCH = "mode_mismatch"
    GENERIC = "generic"


def _load_log(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "events": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("events"), list):
            return raw
    except Exception as exc:
        logger.warning("failure_log read error: %s", exc)
    return {"schema_version": 1, "events": []}


def log_failure(
    failure_class: FailureClass,
    message: str,
    *,
    avenue: str = "global",
    severity: str = "warning",
    pause_recommended: bool = False,
    degrade_recommended: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    path: Optional[Path] = None,
) -> str:
    """
    Record a classified failure. Returns event id.
    Significant failures should surface to CEO review (caller or health reporter).
    """
    p = path or nte_failure_log_path()
    data = _load_log(p)
    events: List[Dict[str, Any]] = list(data.get("events") or [])
    eid = str(uuid.uuid4())
    rec: Dict[str, Any] = {
        "id": eid,
        "ts": time.time(),
        "class": failure_class.value,
        "avenue": avenue,
        "severity": severity,
        "message": message,
        "pause_recommended": pause_recommended,
        "degrade_recommended": degrade_recommended,
        "metadata": metadata or {},
    }
    events.append(rec)
    data["events"] = events[-2000:]
    atomic_write_json(p, data)
    logger.log(
        logging.ERROR if severity == "critical" else logging.WARNING,
        "NTE failure [%s] %s: %s",
        failure_class.value,
        avenue,
        message,
    )
    return eid
