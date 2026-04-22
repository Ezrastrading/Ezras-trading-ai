"""Hard non-live gate for simulation — no venue calls; live env blocked unless artifact authorizes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple


class LiveTradingNotAllowedError(RuntimeError):
    """Raised when simulation is invoked while live-trading env flags are set."""


def nonlive_env_ok(*, runtime_root: Optional[Path] = None) -> Tuple[bool, str]:
    nte_mode = (os.environ.get("NTE_EXECUTION_MODE") or os.environ.get("EZRAS_MODE") or "paper").strip().lower()
    nte_live = (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    cb_enabled = (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    if nte_mode in ("live", "production", "prod") or nte_live or cb_enabled:
        try:
            from trading_ai.control.full_autonomy_mode import is_persisted_full_autonomy_active

            if is_persisted_full_autonomy_active(runtime_root=runtime_root):
                return True, "full_autonomy_active_artifact_authorizes_live_env_for_synthetic_sim"
        except Exception:
            pass
        return False, "live_execution_env_detected"
    return True, "ok"


def assert_nonlive_for_simulation(*, runtime_root: Optional[Path] = None) -> None:
    """
    Fail closed if any live flag is enabled, unless ``data/control/full_autonomy_mode.json``
    declares ``FULL_AUTONOMY_ACTIVE`` (synthetic simulation remains venue-free).
    """
    ok, why = nonlive_env_ok(runtime_root=runtime_root)
    if not ok:
        raise LiveTradingNotAllowedError(
            "Simulation blocked: set NTE_EXECUTION_MODE to non-live, "
            "NTE_LIVE_TRADING_ENABLED=false, COINBASE_EXECUTION_ENABLED=false "
            f"(reason={why})"
        )
