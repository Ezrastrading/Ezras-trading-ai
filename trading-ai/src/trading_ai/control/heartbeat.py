"""
Lightweight consistency checks — alerts only (no auto-halt).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def run_heartbeat_check() -> None:
    """
    Compare positions count vs last snapshot heuristic; alert on obvious mismatch.
    Does not halt.
    """
    try:
        from trading_ai.control.alerts import emit_alert
        from trading_ai.shark.state_store import load_positions

        pos = load_positions()
        n_open = len(pos.get("open_positions") or [])

        stale_path = None
        try:
            from trading_ai.control.paths import control_data_dir

            stale_path = control_data_dir() / "heartbeat_last.json"
        except Exception:
            pass

        prev: Dict[str, Any] = {}
        if stale_path and stale_path.is_file():
            try:
                prev = json.loads(stale_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                prev = {}

        prev_n = int(prev.get("open_positions_n") or -1)
        if prev_n >= 0 and abs(n_open - prev_n) > 25:
            emit_alert(
                "CRITICAL",
                f"heartbeat: open_positions jump {prev_n} -> {n_open} (sanity check)",
            )

        if stale_path:
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_text(
                json.dumps({"open_positions_n": n_open, "ts_unix": time.time()}, indent=2),
                encoding="utf-8",
            )
    except Exception as exc:
        logger.debug("run_heartbeat_check: %s", exc)
