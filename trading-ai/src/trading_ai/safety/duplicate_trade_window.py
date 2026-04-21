"""Canonical duplicate-trade-window parsing — no truthiness on 0; explicit disable vs unset vs seconds."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

DuplicateWindowKind = Literal["unset", "disabled", "seconds"]


@dataclass(frozen=True)
class DuplicateTradeWindowResolution:
    """Single source of truth for how duplicate-trade guarding behaves."""

    kind: DuplicateWindowKind
    """unset → use default_seconds; disabled → skip duplicate guard; seconds → use window_seconds."""

    window_seconds: float
    """Meaningful when kind=='seconds' (may be 0.0). Ignored when unset/disabled."""

    default_seconds: float = 45.0
    """Applied when kind is unset."""

    env_raw_sec: Optional[str] = None
    """Raw EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC value if present."""

    disabled_env_raw: Optional[str] = None
    """Raw EZRAS_FAILSAFE_DUPLICATE_WINDOW_DISABLED value if present."""

    parse_note: Optional[str] = None


def _truthy_disabled(val: Optional[str]) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on", "disabled", "off_duplicate_guard")


def parse_duplicate_trade_window_from_env(
    *,
    environ: Optional[dict] = None,
    default_seconds: float = 45.0,
) -> DuplicateTradeWindowResolution:
    """
    Env:
    - EZRAS_FAILSAFE_DUPLICATE_WINDOW_DISABLED=1|true|yes → duplicate guard off (not the same as window 0).
    - EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC → explicit seconds including 0; must parse as float.
    Missing SEC → unset (fallback default_seconds).
    Invalid SEC → kind=unset, parse_note explains; caller may still use default.
    """
    env = environ if environ is not None else os.environ
    dis_raw = env.get("EZRAS_FAILSAFE_DUPLICATE_WINDOW_DISABLED")
    if _truthy_disabled(dis_raw):
        return DuplicateTradeWindowResolution(
            kind="disabled",
            window_seconds=0.0,
            default_seconds=default_seconds,
            env_raw_sec=env.get("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC"),
            disabled_env_raw=dis_raw,
            parse_note="duplicate_guard_disabled_by_env",
        )

    raw_sec = env.get("EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC")
    if raw_sec is None:
        return DuplicateTradeWindowResolution(
            kind="unset",
            window_seconds=0.0,
            default_seconds=default_seconds,
            env_raw_sec=None,
            disabled_env_raw=dis_raw,
            parse_note=None,
        )

    s = str(raw_sec).strip()
    if s == "":
        return DuplicateTradeWindowResolution(
            kind="unset",
            window_seconds=0.0,
            default_seconds=default_seconds,
            env_raw_sec=raw_sec,
            disabled_env_raw=dis_raw,
            parse_note="empty_EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC_treated_as_unset",
        )

    try:
        sec = float(s)
        if sec < 0:
            return DuplicateTradeWindowResolution(
                kind="unset",
                window_seconds=0.0,
                default_seconds=default_seconds,
                env_raw_sec=raw_sec,
                disabled_env_raw=dis_raw,
                parse_note="negative_duplicate_window_invalid_using_default",
            )
        return DuplicateTradeWindowResolution(
            kind="seconds",
            window_seconds=sec,
            default_seconds=default_seconds,
            env_raw_sec=raw_sec,
            disabled_env_raw=dis_raw,
            parse_note=None,
        )
    except (TypeError, ValueError):
        return DuplicateTradeWindowResolution(
            kind="unset",
            window_seconds=0.0,
            default_seconds=default_seconds,
            env_raw_sec=raw_sec,
            disabled_env_raw=dis_raw,
            parse_note="invalid_float_EZRAS_FAILSAFE_DUPLICATE_WINDOW_SEC_using_default",
        )


def effective_duplicate_window_seconds(res: DuplicateTradeWindowResolution) -> Optional[float]:
    """
    Seconds to use for (now - ts) < window check.
    Returns None when duplicate guard should be skipped entirely.
    """
    if res.kind == "disabled":
        return None
    if res.kind == "unset":
        return float(res.default_seconds)
    return float(res.window_seconds)


def merge_resolution_into_failsafe_state(
    state: dict,
    *,
    environ: Optional[dict] = None,
    default_seconds: float = 45.0,
) -> DuplicateTradeWindowResolution:
    """Attach canonical duplicate-window metadata to failsafe state dict (mutates)."""
    res = parse_duplicate_trade_window_from_env(environ=environ, default_seconds=default_seconds)
    eff = effective_duplicate_window_seconds(res)
    state["duplicate_window_kind"] = res.kind
    state["duplicate_window_effective_sec"] = eff
    state["duplicate_window_env_raw_sec"] = res.env_raw_sec
    state["duplicate_window_parse_note"] = res.parse_note
    if res.kind == "seconds":
        state["duplicate_window_sec"] = res.window_seconds
    elif res.kind == "disabled":
        state["duplicate_window_sec"] = None
    else:
        state["duplicate_window_sec"] = float(default_seconds)
    return res


def persisted_seconds_for_duplicate_check(
    state: dict,
    *,
    environ: Optional[dict] = None,
    default_seconds: float = 45.0,
) -> Tuple[Optional[float], DuplicateTradeWindowResolution]:
    """
    Prefer env resolution; if unset, use persisted duplicate_window_sec when set (allows 0.0).
    Returns (seconds or None if disabled, resolution).
    """
    res = parse_duplicate_trade_window_from_env(environ=environ, default_seconds=default_seconds)
    eff = effective_duplicate_window_seconds(res)
    if res.kind != "unset":
        return eff, res
    # unset: optional file override — explicit 0 is valid; None/missing uses default
    if "duplicate_window_sec" in state and state.get("duplicate_window_sec") is not None:
        try:
            v = float(state["duplicate_window_sec"])
            if v < 0:
                return float(default_seconds), res
            return v, res
        except (TypeError, ValueError):
            return float(default_seconds), res
    return float(default_seconds), res
