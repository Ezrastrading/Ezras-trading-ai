"""Margin borrowing caps — never exceed a safe % of capital; strict slot + drawdown rules."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _margin_state_path() -> Path:
    from trading_ai.governance.storage_architecture import shark_state_path

    return shark_state_path("margin_state.json")


def _load_margin_state() -> Dict[str, Any]:
    p = _margin_state_path()
    if not p.is_file():
        return {"currently_borrowed": 0.0, "open_margin_positions": 0}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("currently_borrowed", 0.0)
            raw.setdefault("open_margin_positions", 0)
            return raw
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"currently_borrowed": 0.0, "open_margin_positions": 0}


def _save_margin_state(data: Dict[str, Any]) -> None:
    p = _margin_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def effective_margin_pct_cap(capital: float, confidence: float) -> float:
    """Public: max margin (borrow) as a fraction of capital for this tier + confidence."""
    return _tier_max_margin_pct(capital, confidence)


def _tier_max_margin_pct(capital: float, confidence: float) -> float:
    """Max fraction of capital that may be borrowed (notional above cash) under tier + confidence rules."""
    if capital < 100:
        return 0.20
    if capital < 1_000:
        return 0.15
    if capital < 25_000:
        return 0.10 if confidence > 0.80 else 0.07
    if capital < 100_000:
        return 0.08 if confidence > 0.85 else 0.05
    return 0.05 if confidence > 0.90 else 0.0


def _tier_allows_margin(hunt_tier: str) -> bool:
    u = (hunt_tier or "").upper()
    if "TIER_C" in u:
        return False
    return "TIER_A" in u or "TIER_B" in u


def get_margin_allowance(
    capital: float,
    confidence: float,
    hunt_tier: str,
    current_drawdown_pct: float,
    *,
    near_zero_hunt: bool = False,
) -> float:
    """
    Maximum additional USD the system may borrow for this trade (notional above cash capital).
    Returns 0.0 if margin is disallowed.
    """
    if near_zero_hunt:
        return 0.0
    if not _tier_allows_margin(hunt_tier):
        return 0.0
    if current_drawdown_pct > 0.15 + 1e-12:
        return 0.0
    st = _load_margin_state()
    if int(st.get("open_margin_positions", 0)) >= 1:
        return 0.0
    max_pct = _tier_max_margin_pct(capital, confidence)
    max_borrowed = float(capital) * max_pct
    cur = float(st.get("currently_borrowed", 0.0))
    return max(0.0, max_borrowed - cur)


def check_margin_safety(
    proposed_position: float,
    available_capital: float,
    margin_allowance: float,
) -> bool:
    """True only if proposed notional fits within cash + allowed borrow."""
    return proposed_position <= available_capital + margin_allowance + 1e-9


def record_margin_position_open(borrowed: float) -> None:
    """Call after a fill that uses margin (borrowed > 0)."""
    if borrowed <= 1e-9:
        return
    st = _load_margin_state()
    st["currently_borrowed"] = float(borrowed)
    st["open_margin_positions"] = 1
    _save_margin_state(st)


def release_margin_after_close() -> None:
    """Call when a margin-backed position is closed."""
    _save_margin_state({"currently_borrowed": 0.0, "open_margin_positions": 0})


def get_margin_status() -> Dict[str, Any]:
    from trading_ai.shark.state_store import load_capital

    rec = load_capital()
    capital = float(rec.current_capital)
    deposited = float(rec.starting_capital)
    st = _load_margin_state()
    cur_borrowed = float(st.get("currently_borrowed", 0.0))
    max_pct = _tier_max_margin_pct(capital, 1.0)
    max_borrowable = capital * max_pct
    remaining = max(0.0, max_borrowable - cur_borrowed)
    slot_open = int(st.get("open_margin_positions", 0)) >= 1
    margin_allowed = (not slot_open) and max_borrowable > 1e-9 and deposited > 0
    return {
        "deposited_capital": deposited,
        "max_borrowable": max_borrowable,
        "currently_borrowed": cur_borrowed,
        "remaining_margin": remaining,
        "margin_pct": (cur_borrowed / deposited) if deposited > 1e-9 else 0.0,
        "margin_allowed": margin_allowed,
    }
