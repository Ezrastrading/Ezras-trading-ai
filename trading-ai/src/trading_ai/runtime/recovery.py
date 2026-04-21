"""Boot-time recovery: reconcile live state vs ledger open trades."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime.live_execution_state import read_live_execution_state
from trading_ai.runtime.trade_ledger import iter_ledger_lines
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _rel() -> str:
    return "data/control/recovery_log.json"


def run_recovery_audit(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    On boot: load last live state + last ledger rows; flag mid-flight or manual intervention.
    Does not place orders.
    """
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    live = read_live_execution_state(runtime_root=runtime_root)
    last_lines: List[Dict[str, Any]] = []
    for row in iter_ledger_lines(runtime_root=runtime_root, max_lines=200):
        last_lines.append(row)
    last_lines = last_lines[-50:]

    open_like: List[Dict[str, Any]] = []
    for row in reversed(last_lines):
        st = str(row.get("execution_status") or "").lower()
        if st in ("open", "buy_placed", "pending_sell", "mid_flight"):
            open_like.append(row)

    mid_flight = bool(open_like) or (
        str(live.get("last_action") or "").startswith("order")
        and live.get("last_success") is not True
    )

    outcome: Dict[str, Any] = {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "live_state_snapshot": {
            "last_action": live.get("last_action"),
            "last_trade_id": live.get("last_trade_id"),
            "last_success": live.get("last_success"),
            "mode": live.get("current_mode"),
        },
        "suspected_mid_flight": mid_flight,
        "open_like_ledger_rows": open_like[:5],
        "resolution": (
            "manual_intervention_required"
            if mid_flight
            else "no_action_required"
        ),
        "notes": (
            "If buy happened but sell did not, reconcile venue positions and close or label manually."
            if mid_flight
            else "No inconsistent state detected from artifacts alone."
        ),
    }
    ad.write_json(_rel(), outcome)
    return outcome
