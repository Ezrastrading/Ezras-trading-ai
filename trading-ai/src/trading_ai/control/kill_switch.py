"""
Operator kill switch: touch ``EZRAS_RUNTIME_ROOT/KILL_SWITCH`` to halt all new entries.

Does not close positions; blocks new orders via system_guard halt + preflight checks.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def kill_switch_path() -> Path:
    from trading_ai.control.paths import kill_switch_path as _p

    return _p()


def kill_switch_active() -> bool:
    """
    True if the kill-switch file exists. Triggers ``halt_now("MANUAL_OPERATOR_HALT")``
    when the file is present and trading is not already halted (idempotent).
    """
    try:
        p = kill_switch_path()
        if not p.is_file():
            return False
        from trading_ai.core.system_guard import get_system_guard

        sg = get_system_guard()
        if not sg.is_trading_halted():
            sg.halt_now("MANUAL_OPERATOR_HALT")
        return True
    except Exception as exc:
        logger.debug("kill_switch_active: %s", exc)
        return False


def trading_blocked_by_kill_switch() -> bool:
    """Alias for preflight: same semantics as :func:`kill_switch_active`."""
    return kill_switch_active()
