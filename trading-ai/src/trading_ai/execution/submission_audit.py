"""Append-only venue execution-intent log under ``EZRAS_RUNTIME_ROOT`` / ``logs``."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)


def execution_submission_log_path() -> Path:
    return runtime_root() / "logs" / "execution_submission_log.md"


def append_execution_submission_log(
    *,
    trade_id: str,
    requested_size: Any,
    approved_size: Any,
    actual_submitted_size: Any,
    bucket: Any,
    approval_status: Any,
    trading_allowed: Any,
    reason: Any,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Structured snapshot written **before** venue submit (or before abort when blocked).

    ``actual_submitted_size`` is venue-specific (e.g. Kalshi contract ``count``); use ``0`` when no order is sent.
    """
    try:
        ts = datetime.now(timezone.utc).isoformat()
        row: Dict[str, Any] = {
            "timestamp": ts,
            "event_type": "execution_submission_intent",
            "trade_id": trade_id,
            "requested_size": requested_size,
            "approved_size": approved_size,
            "actual_submitted_size": actual_submitted_size,
            "bucket": bucket,
            "approval_status": approval_status,
            "trading_allowed": trading_allowed,
            "reason": reason,
        }
        if extra:
            row.update(extra)
        p = execution_submission_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        block = (
            f"\n## {ts} — execution_submission — {trade_id}\n\n"
            f"```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        )
        with p.open("a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("append_execution_submission_log failed: %s", exc)
