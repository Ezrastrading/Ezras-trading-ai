"""Per-avenue capital allocation from ledger + scores (read-mostly)."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.nte.nte_global.capital_ledger import load_ledger


def allocation_snapshot() -> Dict[str, Any]:
    led = load_ledger()
    return {
        "per_avenue_allocations": dict(led.get("per_avenue_allocations") or {}),
        "per_avenue_realized_net": dict(led.get("per_avenue_realized_net") or {}),
        "per_avenue_unrealized": dict(led.get("per_avenue_unrealized") or {}),
        "available_cash": float(led.get("available_cash") or 0.0),
        "reserved_cash": float(led.get("reserved_cash") or 0.0),
    }
