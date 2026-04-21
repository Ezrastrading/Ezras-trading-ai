"""Sudden move detection for exit priority."""

from __future__ import annotations

from typing import Any, Dict, Optional


def detect_sudden_move(
    *,
    last_price: float,
    prev_price: Optional[float],
    sudden_drop_pct: float,
    sudden_spike_pct: float,
) -> Dict[str, Any]:
    if not last_price or prev_price is None or prev_price <= 0:
        return {"sudden_drop": False, "sudden_spike": False}
    chg = (last_price - float(prev_price)) / float(prev_price)
    return {
        "sudden_drop": chg <= -abs(sudden_drop_pct),
        "sudden_spike": chg >= abs(sudden_spike_pct),
        "pct_change": chg,
    }
