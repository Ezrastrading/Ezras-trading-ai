"""Append-only equity curve for operators."""

from __future__ import annotations

import csv
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def record_equity_point() -> None:
    """Append ``timestamp,total_equity`` to ``data/control/equity_curve.csv``."""
    try:
        from trading_ai.control.paths import equity_curve_csv_path
        from trading_ai.shark.state_store import load_capital

        eq = float(load_capital().current_capital or 0.0)
        p = equity_curve_csv_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        new_file = not p.is_file()
        ts = datetime.now(timezone.utc).isoformat()
        with p.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["timestamp", "total_equity"])
            w.writerow([ts, f"{eq:.6f}"])
    except Exception as exc:
        logger.debug("record_equity_point: %s", exc)
