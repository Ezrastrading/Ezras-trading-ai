"""Immediate process halt on reality-lock violations (no retries, no overrides)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def reality_halt(reason: str) -> None:
    """Persist trading halt via system guard; caller must not continue trading."""
    try:
        from trading_ai.core.system_guard import get_system_guard

        get_system_guard().halt_now(f"reality_lock:{reason}")
    except Exception as exc:
        logger.critical("reality_halt failed to persist: %s (%s)", reason, exc)


def raise_and_halt(exc_type: type[BaseException], message: str) -> None:
    """Halt then raise — ensures halt is recorded even if exception is caught upstream."""
    reality_halt(message)
    raise exc_type(message)
