"""Trade audit summary from NTE trade memory."""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.nte.memory.store import MemoryStore


def build_trade_audit(limit: int = 50) -> Dict[str, Any]:
    store = MemoryStore()
    store.ensure_defaults()
    tm = store.load_json("trade_memory.json")
    trades: List[Any] = tm.get("trades") or []
    return {"count": len(trades), "recent": trades[-limit:]}
