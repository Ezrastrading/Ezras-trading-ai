"""Bootstrap position cap — first N closed trades use a stricter max concurrent opens (measurement + guard)."""

from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_BOOTSTRAP_TRADE_COUNT = 20
_BOOTSTRAP_MAX_OPEN = 3


def closed_trade_count() -> int:
    try:
        from trading_ai.nte.databank.local_trade_store import load_all_trade_events

        return len(load_all_trade_events())
    except Exception as exc:
        logger.debug("position_control: trade count unavailable (%s) — no bootstrap cap", exc)
        return _BOOTSTRAP_TRADE_COUNT + 1


def effective_max_open_positions(base_max: int) -> int:
    """
    During the first ``_BOOTSTRAP_TRADE_COUNT`` completed trades (exclusive of future),
    cap concurrent opens at ``_BOOTSTRAP_MAX_OPEN``. Afterwards use ``base_max`` unchanged.
    """
    b = max(1, int(base_max))
    n = closed_trade_count()
    if n < _BOOTSTRAP_TRADE_COUNT:
        return min(_BOOTSTRAP_MAX_OPEN, b)
    return b


def position_cap_blocks_new_entry(current_open: int, base_max: int) -> Tuple[bool, str]:
    """
    Returns (blocked, reason). When blocked, caller should skip new entry and may emit INFO alert once.
    """
    eff = effective_max_open_positions(base_max)
    if current_open < eff:
        return False, ""
    if closed_trade_count() < _BOOTSTRAP_TRADE_COUNT:
        return True, "bootstrap_position_cap"
    return True, "max_open_positions"
