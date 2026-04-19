"""Daily loss cap, consecutive-loss pause, position fraction from reward multiplier."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RiskView:
    paused: bool
    reason: str
    daily_pnl_pct: float
    consecutive_losses: int
    size_fraction: float


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _roll_day(state: Dict[str, Any]) -> Dict[str, Any]:
    d = str(_today_utc())
    if state.get("day_utc") != d:
        state["day_utc"] = d
        state["day_start_equity"] = None
        state["day_realized_pnl_usd"] = 0.0
    return state


def evaluate_risk(
    *,
    state: Dict[str, Any],
    equity: float,
    settings: Any,
    reward: Dict[str, Any],
) -> RiskView:
    state = _roll_day(state)
    start = state.get("day_start_equity")
    if start is None or float(start) <= 0:
        state["day_start_equity"] = float(equity)
        start = float(equity)

    day_pnl = float(state.get("day_realized_pnl_usd") or 0.0)
    pnl_pct = day_pnl / float(start) if start > 0 else 0.0

    cl = int(state.get("consecutive_losses") or 0)
    paused_until = state.get("paused_until")
    if paused_until:
        try:
            ts = float(paused_until)
            if datetime.now(timezone.utc).timestamp() < ts:
                return RiskView(
                    True,
                    "paused_after_loss_streak",
                    pnl_pct,
                    cl,
                    0.0,
                )
        except (TypeError, ValueError):
            pass

    dl_max = float(getattr(settings, "daily_loss_max", 0.06))
    if pnl_pct <= -dl_max:
        logger.warning("NTE risk: daily loss cap hit (%.4f <= -%.4f)", pnl_pct, dl_max)
        return RiskView(True, "daily_loss_cap", pnl_pct, cl, 0.0)

    max_cl = int(getattr(settings, "max_consecutive_losses_pause", 4))
    if cl >= max_cl:
        return RiskView(True, "consecutive_losses", pnl_pct, cl, 0.0)

    mult = float(reward.get("size_multiplier") or 1.0)
    mult = max(0.35, min(1.25, mult))
    lo = float(getattr(settings, "size_pct_min", 0.15)) * mult
    hi = float(getattr(settings, "size_pct_max", 0.25)) * mult
    frac = (lo + hi) / 2.0
    return RiskView(False, "ok", pnl_pct, cl, frac)


def register_closed_trade_pnl(
    state: Dict[str, Any],
    pnl_usd: float,
    equity_before: float,
    settings: Any,
) -> None:
    state = _roll_day(state)
    state["day_realized_pnl_usd"] = float(state.get("day_realized_pnl_usd") or 0.0) + float(
        pnl_usd
    )
    cl = int(state.get("consecutive_losses") or 0)
    if pnl_usd < 0:
        state["consecutive_losses"] = cl + 1
    else:
        state["consecutive_losses"] = 0

    max_cl = int(getattr(settings, "max_consecutive_losses_pause", 4))
    if int(state["consecutive_losses"]) >= max_cl:
        import time

        state["paused_until"] = time.time() + 86400.0
        logger.warning("NTE: pausing ~24h after %s consecutive losses", max_cl)

    start = float(state.get("day_start_equity") or equity_before or 1.0)
    pnl_pct = float(state.get("day_realized_pnl_usd") or 0.0) / start if start > 0 else 0.0
    dl_max = float(getattr(settings, "daily_loss_max", 0.06))
    if pnl_pct <= -dl_max:
        import time

        state["paused_until"] = time.time() + 43200.0
        logger.warning("NTE: cooling off ~12h after daily loss cap")
