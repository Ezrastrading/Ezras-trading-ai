"""
Shared closed-trade execution reconciliation — single entry point for all close paths.

Calls :func:`record_execution_close` with derived PnL/fees when present on the trade dict.
Never raises; returns a status dict for post-trade / audit.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _derive_realized_pnl_dollars(trade: Dict[str, Any]) -> Optional[float]:
    if trade.get("realized_pnl_dollars") is not None:
        try:
            return float(trade["realized_pnl_dollars"])
        except (TypeError, ValueError):
            pass
    cap = trade.get("capital_allocated") or trade.get("size_dollars")
    roi = trade.get("roi_percent")
    if cap is not None and roi is not None:
        try:
            return float(cap) * float(roi) / 100.0
        except (TypeError, ValueError):
            pass
    return None


def _derive_fees(trade: Dict[str, Any]) -> Optional[float]:
    for k in ("fees_total", "execution_fees", "commission_dollars", "fees"):
        v = trade.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def reconcile_closed_trade_execution(trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persist close row into execution reconciliation state and append log.

    Safe to call from :func:`execute_post_trade_closed` and any other close sink.
    """
    tid = str(trade.get("trade_id") or "").strip()
    if not tid:
        return {"ok": False, "error": "missing_trade_id"}
    pnl = _derive_realized_pnl_dollars(trade)
    fees = _derive_fees(trade)
    try:
        from trading_ai.execution.execution_reconciliation import record_execution_close

        row = record_execution_close(
            trade_id=tid,
            realized_pnl=pnl,
            fees_total=fees,
            extra={
                "result": trade.get("result"),
                "exit_reason": trade.get("exit_reason"),
                "source": "close_trade_reconciliation",
            },
        )
        return {"ok": True, "record_execution_close": row}
    except Exception as exc:
        logger.warning("reconcile_closed_trade_execution failed: %s", exc)
        return {"ok": False, "error": str(exc)}
