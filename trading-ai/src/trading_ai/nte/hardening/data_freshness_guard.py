"""Guard against stale market or feed timestamps."""

from __future__ import annotations

import time
from typing import Optional

from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure


def is_stale(
    last_ts: Optional[float],
    max_age_sec: float,
    *,
    avenue: str = "global",
    label: str = "data",
) -> bool:
    if last_ts is None or last_ts <= 0:
        return True
    return (time.time() - float(last_ts)) > max_age_sec


def require_fresh(
    last_ts: Optional[float],
    max_age_sec: float,
    *,
    avenue: str = "global",
    label: str = "market_data",
    log_on_stale: bool = True,
) -> bool:
    """Return True if fresh; if stale, optionally log and return False."""
    stale = is_stale(last_ts, max_age_sec, avenue=avenue, label=label)
    if stale and log_on_stale:
        log_failure(
            FailureClass.STALE_DATA,
            f"{label} older than {max_age_sec}s",
            avenue=avenue,
            severity="warning",
            degrade_recommended=True,
            metadata={"last_ts": last_ts},
        )
    return not stale
