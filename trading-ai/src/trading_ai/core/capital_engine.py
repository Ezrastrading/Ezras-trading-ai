"""
Account-level sizing and risk limits before orders.

Exposure and drawdown checks use the same units: USD notional vs account balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


@dataclass
class CapitalLimits:
    """Default policy: 2% trade with $10 floor, 10% cap; portfolio clamps."""

    trade_size_default_pct: float = 0.02
    trade_size_min_usd: float = 10.0
    trade_size_max_pct: float = 0.10
    max_total_exposure_pct: float = 0.30
    max_per_trade_pct: float = 0.10
    max_drawdown_stop_daily_pct: float = 0.10


@dataclass
class CapitalEngine:
    """
    Tracks balance, open exposure, and daily PnL for pre-trade gating.

    ``daily_pnl`` is today's realized-style change vs ``day_start_balance`` (caller updates).
    """

    current_balance: float = 0.0
    open_exposure: float = 0.0
    daily_pnl: float = 0.0
    day_start_balance: float = 0.0
    day_utc: Optional[str] = None
    limits: CapitalLimits = field(default_factory=CapitalLimits)

    def roll_day_if_needed(self) -> None:
        d = str(_today_utc())
        if self.day_utc != d:
            self.day_utc = d
            self.day_start_balance = float(self.current_balance) if self.current_balance > 0 else 0.0
            self.daily_pnl = 0.0

    def daily_drawdown_ratio(self) -> float:
        """Negative when losing; e.g. -0.10 means -10% on the day."""
        self.roll_day_if_needed()
        start = float(self.day_start_balance)
        if start <= 0:
            return 0.0
        return float(self.daily_pnl) / start

    @staticmethod
    def get_trade_size(balance_usd: float, limits: Optional[CapitalLimits] = None) -> float:
        """Default 2% of balance, floor $10, cap 10% of balance."""
        lim = limits or CapitalLimits()
        b = max(0.0, float(balance_usd))
        if b <= 0:
            return 0.0
        raw = b * float(lim.trade_size_default_pct)
        out = max(float(lim.trade_size_min_usd), raw)
        out = min(out, b * float(lim.trade_size_max_pct))
        return out

    def enforce_limits(
        self,
        *,
        proposed_trade_usd: float,
        account_balance_usd: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Returns ``(ok, reason)``. Does not mutate state except day roll.

        - ``max_total_exposure``: open_exposure + proposed <= 30% of account
        - ``max_per_trade``: proposed <= 10% of account
        - ``max_drawdown_stop``: block if daily loss exceeds 10% of day start
        """
        self.roll_day_if_needed()
        acct = float(account_balance_usd) if account_balance_usd is not None else float(
            self.current_balance
        )
        acct = max(acct, 1e-9)
        lim = self.limits
        pt = max(0.0, float(proposed_trade_usd))

        if pt > acct * float(lim.max_per_trade_pct) + 1e-9:
            return False, "max_per_trade"

        if float(self.open_exposure) + pt > acct * float(lim.max_total_exposure_pct) + 1e-9:
            return False, "max_total_exposure"

        dd = self.daily_drawdown_ratio()
        if dd <= -float(lim.max_drawdown_stop_daily_pct) - 1e-12:
            return False, "max_drawdown_stop"

        return True, "ok"

    def should_block_trade(
        self,
        *,
        proposed_trade_usd: float,
        account_balance_usd: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """``(blocked, reason)`` — inverse of ``enforce_limits`` ok flag."""
        ok, reason = self.enforce_limits(
            proposed_trade_usd=proposed_trade_usd,
            account_balance_usd=account_balance_usd,
        )
        if ok:
            return False, None
        return True, reason

    def apply_daily_pnl_delta(self, delta_usd: float) -> None:
        self.roll_day_if_needed()
        self.daily_pnl += float(delta_usd)
        self.current_balance += float(delta_usd)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_balance": self.current_balance,
            "open_exposure": self.open_exposure,
            "daily_pnl": self.daily_pnl,
            "day_start_balance": self.day_start_balance,
            "day_utc": self.day_utc,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CapitalEngine":
        e = cls()
        e.current_balance = float(d.get("current_balance") or 0.0)
        e.open_exposure = float(d.get("open_exposure") or 0.0)
        e.daily_pnl = float(d.get("daily_pnl") or 0.0)
        e.day_start_balance = float(d.get("day_start_balance") or 0.0)
        e.day_utc = d.get("day_utc")
        return e


def capital_preflight_block(
    *,
    proposed_trade_usd: float,
    account_balance_usd: float,
    open_exposure_usd: float = 0.0,
    daily_pnl_usd: float = 0.0,
    day_start_balance_usd: float = 0.0,
    limits: Optional[CapitalLimits] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Returns ``(blocked, reason)`` for a proposed order.

    Hydrate ``day_utc`` to today before calling so ``daily_pnl_usd`` / ``day_start_balance_usd``
    from an external ledger are not cleared by day roll.
    """
    ce = CapitalEngine(limits=limits or CapitalLimits())
    d = str(_today_utc())
    ce.day_utc = d
    ce.current_balance = max(0.0, float(account_balance_usd))
    ce.open_exposure = max(0.0, float(open_exposure_usd))
    ce.daily_pnl = float(daily_pnl_usd)
    ds = float(day_start_balance_usd)
    ce.day_start_balance = ds if ds > 0 else ce.current_balance
    blocked, reason = ce.should_block_trade(
        proposed_trade_usd=float(proposed_trade_usd),
        account_balance_usd=float(account_balance_usd),
    )
    return blocked, reason
