"""
Non-blocking hooks: clean ledger + daily/weekly summaries after a closed trade is recorded.

Safe to call from hot paths — never raises.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def run_trader_visibility_after_close(trade_dict: Mapping[str, Any]) -> None:
    """
    Append CSV row and rebuild summaries. Swallows all errors (logs only).
    """
    try:
        # Non-negotiable: asymmetric gate must not contaminate core trader-visible summaries.
        # If you want an asym-visible ledger, create a separate artifact family under data/asymmetric/.
        if str(trade_dict.get("trade_type") or "").strip().lower() == "asymmetric":
            return
        if str(trade_dict.get("gate_family") or "").strip().lower() == "asymmetric":
            return
        from trading_ai.reporting.daily_summary import rebuild_daily_summary
        from trading_ai.reporting.trade_ledger import append_clean_trade_row
        from trading_ai.reporting.weekly_summary import rebuild_weekly_summary

        append_clean_trade_row(trade_dict)
        rebuild_daily_summary()
        rebuild_weekly_summary()
    except Exception as exc:
        logger.warning("trader_visibility: pipeline failed (non-fatal): %s", exc)
