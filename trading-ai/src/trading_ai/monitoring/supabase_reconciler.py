"""Background-friendly replay of unsynced trade rows to Supabase (eventual consistency)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)


def reconcile_loop(sleep_sec: float = 10.0) -> None:
    """Flush local queue on an interval until process exit (operator-supervised worker)."""
    from trading_ai.nte.databank.supabase_trade_sync import flush_unsynced_trades

    while True:
        try:
            rep: Dict[str, Any] = flush_unsynced_trades()
            if rep.get("flushed"):
                logger.info("supabase_reconciler flushed=%s remaining=%s", rep.get("flushed"), rep.get("remaining"))
        except Exception:
            logger.exception("supabase_reconciler tick failed")
        time.sleep(max(1.0, float(sleep_sec)))


def reconcile_once() -> Dict[str, Any]:
    """Single flush (for tests / cron)."""
    from trading_ai.nte.databank.supabase_trade_sync import flush_unsynced_trades

    return flush_unsynced_trades()
