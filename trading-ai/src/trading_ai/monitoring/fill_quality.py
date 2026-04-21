"""Fill latency / partials — measurement + operator alerts (does not change execution)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from trading_ai.control.paths import control_data_dir

logger = logging.getLogger(__name__)


def fill_quality_seconds_threshold() -> float:
    try:
        return float((os.environ.get("FILL_QUALITY_SECONDS_WARNING") or "10").strip())
    except (TypeError, ValueError):
        return 10.0


def fill_quality_log_path() -> Path:
    return control_data_dir() / "fill_quality_log.jsonl"


def evaluate_fill_quality(merged: Mapping[str, Any]) -> Dict[str, Any]:
    fill_s = float(merged.get("fill_seconds") or 0.0)
    partials = int(merged.get("partial_fill_count") or 0)
    stale = bool(merged.get("stale_cancelled") or False)
    thr = fill_quality_seconds_threshold()
    poor = fill_s > thr or partials > 2
    return {
        "fill_seconds": fill_s,
        "partial_fill_count": partials,
        "stale_cancelled": stale,
        "poor_fill_quality": poor,
        "threshold_seconds": thr,
    }


def append_fill_quality_log(merged: Mapping[str, Any], *, evaluation: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    ev = dict(evaluation) if evaluation is not None else evaluate_fill_quality(merged)
    row: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trade_id": merged.get("trade_id"),
        "asset": merged.get("asset"),
        "avenue_name": merged.get("avenue_name"),
        **ev,
    }
    p = fill_quality_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        logger.warning("fill_quality_log append failed: %s", exc)
    if ev.get("poor_fill_quality"):
        try:
            from trading_ai.control.alerts import emit_alert

            emit_alert("WARNING", "Poor fill quality")
        except Exception as exc:
            logger.debug("fill_quality alert skipped: %s", exc)
    return row
