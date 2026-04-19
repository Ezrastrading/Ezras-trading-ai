"""Validate NTE + mode env; fail fast in strict mode."""

from __future__ import annotations

import os
from typing import List, Tuple

from trading_ai.nte.config.settings import NTECoinbaseSettings, load_nte_settings
from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure
from trading_ai.nte.hardening.mode_context import ExecutionMode, get_execution_mode


def validate_nte_settings(s: NTECoinbaseSettings | None = None) -> Tuple[bool, List[str]]:
    s = s or load_nte_settings()
    errors: List[str] = []
    if s.size_pct_min > s.size_pct_max:
        errors.append("size_pct_min > size_pct_max")
    if s.max_open_positions < 1 or s.max_open_positions > 100:
        errors.append("max_open_positions out of sensible range")
    if s.tp_min < 0 or s.sl_min < 0:
        errors.append("negative tp/sl")
    if not (s.avenue_id or "").strip():
        errors.append("missing avenue_id")
    return len(errors) == 0, errors


def validate_mode_safety(strict: bool = False) -> Tuple[bool, List[str]]:
    """Ensure live is not accidentally combined with replay/paper flags."""
    errors: List[str] = []
    mode = get_execution_mode()
    live_flag = (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").lower() in ("1", "true", "yes")
    if mode == ExecutionMode.LIVE and not live_flag:
        errors.append("LIVE mode without NTE_LIVE_TRADING_ENABLED=true")
    if mode == ExecutionMode.REPLAY and live_flag:
        errors.append("replay/backtest with live flag set")
    if strict and errors:
        for e in errors:
            log_failure(FailureClass.CONFIG_INVALID, e, severity="critical", pause_recommended=True)
        raise ValueError("; ".join(errors))
    return len(errors) == 0, errors
