"""Hard non-live gate for simulation — no venue calls, no live execution env."""

from __future__ import annotations

import os
from typing import Tuple


class LiveTradingNotAllowedError(RuntimeError):
    """Raised when simulation is invoked while live-trading env flags are set."""


def nonlive_env_ok() -> Tuple[bool, str]:
    nte_mode = (os.environ.get("NTE_EXECUTION_MODE") or os.environ.get("EZRAS_MODE") or "paper").strip().lower()
    nte_live = (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    cb_enabled = (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    if nte_mode in ("live", "production", "prod") or nte_live or cb_enabled:
        return False, "live_execution_env_detected"
    return True, "ok"


def assert_nonlive_for_simulation() -> None:
    """
    Fail closed if any live flag is enabled.

    Contract (must all be safe for sim):
    - NTE_EXECUTION_MODE != live
    - NTE_LIVE_TRADING_ENABLED != true
    - COINBASE_EXECUTION_ENABLED != true
    """
    ok, why = nonlive_env_ok()
    if not ok:
        raise LiveTradingNotAllowedError(
            "Simulation blocked: set NTE_EXECUTION_MODE to non-live, "
            "NTE_LIVE_TRADING_ENABLED=false, COINBASE_EXECUTION_ENABLED=false"
        )
