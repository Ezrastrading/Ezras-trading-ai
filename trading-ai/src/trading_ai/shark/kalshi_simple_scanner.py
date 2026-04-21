"""Public-safe Kalshi simple scanner shim.

The full Kalshi execution/scanning logic is private. Public smoke tests only need a stable
`run_simple_scan()` import that can no-op safely when configured to do zero trades.
"""

from __future__ import annotations

import os
from typing import Any, Dict


def run_simple_scan() -> Dict[str, Any]:
    enabled = (os.environ.get("KALSHI_SIMPLE_SCAN_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    max_trades_raw = (os.environ.get("KALSHI_SIMPLE_MAX_TRADES") or "").strip()
    try:
        max_trades = int(float(max_trades_raw)) if max_trades_raw else 0
    except Exception:
        max_trades = 0

    if not enabled:
        return {"ok": True, "skipped": True, "reason": "KALSHI_SIMPLE_SCAN_ENABLED not true"}
    if max_trades <= 0:
        return {"ok": True, "skipped": True, "reason": "KALSHI_SIMPLE_MAX_TRADES<=0 (smoke no-op)"}

    raise RuntimeError(
        "Public build kalshi_simple_scanner cannot execute live scans. "
        "Set KALSHI_SIMPLE_MAX_TRADES=0 for no-op smoke, or use private repo for live."
    )

