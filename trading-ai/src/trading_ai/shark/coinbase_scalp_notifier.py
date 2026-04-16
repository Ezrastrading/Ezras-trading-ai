"""
Single-shot notifications for scalp exits (no duplicate profit alerts per trade).

Duplicate prevention is enforced by ``trade['exit_notified']`` in persisted state;
this module only formats and sends the message.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class CoinbaseScalpNotifier:
    """Telegram alerts when enabled; caller gates on ``exit_notified``."""

    def notify_exit(self, trade: Dict[str, Any], summary: str) -> bool:
        if trade.get("exit_notified"):
            return False
        try:
            from trading_ai.shark.reporting import send_telegram

            return bool(send_telegram(summary))
        except Exception as exc:
            logger.warning("scalp exit notify failed: %s", exc)
            return False

    def notify_entry(self, summary: str) -> bool:
        try:
            from trading_ai.shark.reporting import send_telegram

            return bool(send_telegram(summary))
        except Exception as exc:
            logger.debug("scalp entry notify skipped: %s", exc)
            return False
