"""Multi-avenue PnL-based shift hint (best ← worst)."""

from typing import Any, Dict


def adjust_capital_allocation(avenue_pnls: Dict[str, Any]) -> Dict[str, Any]:
    if not avenue_pnls:
        return {"shift_from": "", "shift_to": "", "fraction": 0.0}
    best = max(avenue_pnls, key=avenue_pnls.get)
    worst = min(avenue_pnls, key=avenue_pnls.get)
    return {
        "shift_from": worst,
        "shift_to": best,
        "fraction": 0.1,
    }
