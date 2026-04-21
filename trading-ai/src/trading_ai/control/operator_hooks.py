"""
Operator-layer refresh hooks — safe no-ops on failure.

Called after trade resolution and optionally from command-center cycles.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def refresh_operator_artifacts_after_trade(
    *,
    trade_id: Optional[str] = None,
    pnl_usd: Optional[float] = None,
    outlet: str = "",
) -> None:
    """Regenerate live status, equity curve, daily PnL files, optional trade explainer."""
    try:
        from trading_ai.control.live_status import write_live_status_snapshot

        write_live_status_snapshot()
    except Exception:
        logger.debug("operator live_status skipped", exc_info=True)
    try:
        from trading_ai.control.equity_curve import record_equity_point

        record_equity_point()
    except Exception:
        logger.debug("operator equity_curve skipped", exc_info=True)
    try:
        from trading_ai.control.pnl_reports import regenerate_daily_pnl_reports

        regenerate_daily_pnl_reports()
    except Exception:
        logger.debug("operator pnl_reports skipped", exc_info=True)
    if trade_id:
        try:
            from trading_ai.control.trade_explainer import explain_trade

            explain_trade(str(trade_id))
        except Exception:
            logger.debug("operator trade_explainer skipped", exc_info=True)
    try:
        from trading_ai.control.alerts import emit_alert

        tid = str(trade_id or "").strip() or "unknown"
        p = "" if pnl_usd is None else f" pnl={float(pnl_usd):+.2f}"
        emit_alert("INFO", f"trade_closed id={tid} venue={outlet}{p}")
    except Exception:
        logger.debug("operator alert trade_closed skipped", exc_info=True)
