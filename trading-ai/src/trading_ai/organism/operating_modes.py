"""Pressure vs opportunity — adjusts global risk posture."""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Tuple

from trading_ai.nte.utils.atomic_json import atomic_write_json
from trading_ai.organism.paths import operating_mode_path


class OperatingMode(str, Enum):
    NORMAL = "normal"
    PRESSURE = "pressure"
    OPPORTUNITY = "opportunity"


def _defaults() -> Dict[str, Any]:
    return {"mode": OperatingMode.NORMAL.value, "reason": "", "updated_at": None}


def load_mode_state(path: Path | None = None) -> Dict[str, Any]:
    p = path or operating_mode_path()
    if not p.is_file():
        return _defaults()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return _defaults()


def save_mode_state(state: Dict[str, Any], path: Path | None = None) -> None:
    p = path or operating_mode_path()
    atomic_write_json(p, dict(state))


def resolve_operating_mode(
    *,
    rolling_expectancy: float,
    recent_drawdown_ratio: float,
    pipeline_ok: bool,
) -> Tuple[OperatingMode, str]:
    """
    PRESSURE: bad expectancy, large drawdown, or pipeline stress.

    OPPORTUNITY: strong rolling expectancy with controlled drawdown and healthy pipeline.
    """
    if not pipeline_ok:
        return OperatingMode.PRESSURE, "pipeline_degraded"
    if rolling_expectancy < 0 or recent_drawdown_ratio > 0.45:
        return OperatingMode.PRESSURE, "expectancy_or_drawdown"
    if rolling_expectancy > 0.02 and recent_drawdown_ratio < 0.15:
        return OperatingMode.OPPORTUNITY, "strong_consistency"
    return OperatingMode.NORMAL, "balanced"


def env_override_mode() -> OperatingMode | None:
    raw = (os.environ.get("ACCO_OPERATING_MODE") or "").strip().lower()
    if raw == "pressure":
        return OperatingMode.PRESSURE
    if raw == "opportunity":
        return OperatingMode.OPPORTUNITY
    if raw == "normal":
        return OperatingMode.NORMAL
    return None
