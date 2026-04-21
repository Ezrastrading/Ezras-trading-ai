"""Append-only operator alerts (high signal)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_LEVELS = frozenset({"INFO", "WARNING", "CRITICAL"})


def emit_alert(level: str, message: str) -> None:
    """
    Append one line: ``[TIMESTAMP] LEVEL: MESSAGE``

    Never raises.
    """
    try:
        lv = str(level or "INFO").strip().upper()
        if lv not in _LEVELS:
            lv = "INFO"
        msg = str(message or "").replace("\n", " ").strip()[:500]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{ts}] {lv}: {msg}\n"
        from trading_ai.control.paths import alerts_txt_path

        p = alerts_txt_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        logger.debug("emit_alert failed: %s", exc)
