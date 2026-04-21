"""
Callbacks and structured logging for the Kalshi scalp engine (Telegram / custom hooks optional).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class KalshiScalpNotifier:
    """
    Default notifier: JSON-style lines to the scalp logger.

    Replace or wrap with Telegram / webhooks by passing ``extra_sink``.
    """

    def __init__(self, extra_sink: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.extra_sink = extra_sink

    def _emit(self, payload: Dict[str, Any]) -> None:
        logger.info("kalshi_scalp %s", json.dumps(payload, default=str))
        if self.extra_sink:
            try:
                self.extra_sink(payload)
            except Exception as exc:
                logger.debug("scalp extra_sink failed: %s", exc)

    def on_engine_cycle(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "engine_cycle", **payload})

    def on_scan_report(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "scan", **payload})

    def on_entry_submitted(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "entry_submitted", **payload})

    def on_entry_filled(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "entry_filled", **payload})

    def on_position_check(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "position_check", **payload})

    def on_exit_submitted(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "exit_submitted", **payload})

    def on_exit_filled(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "exit_filled", **payload})

    def on_duplicate_exit_prevented(self, payload: Dict[str, Any]) -> None:
        self._emit({"event": "duplicate_exit_prevented", **payload})
