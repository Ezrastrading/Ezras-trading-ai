"""Per-venue capital isolation — no shared pool; shutdown one avenue without affecting others."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class VenueState:
    """Isolated state for one execution venue."""

    venue_id: str
    capital_usd: float = 0.0
    open_positions: int = 0
    pnl_usd: float = 0.0
    risk_limits: Dict[str, Any] = field(default_factory=dict)
    shutdown_flag: bool = False

    def allow_trading(self) -> bool:
        return not self.shutdown_flag


_venues: Dict[str, VenueState] = {}


def get_venue_state(venue_id: str) -> VenueState:
    vid = (venue_id or "unknown").strip().lower()
    if vid not in _venues:
        _venues[vid] = VenueState(venue_id=vid)
    return _venues[vid]


def set_venue_shutdown(venue_id: str, *, reason: str = "") -> None:
    v = get_venue_state(venue_id)
    v.shutdown_flag = True
    v.risk_limits["last_shutdown_reason"] = reason
