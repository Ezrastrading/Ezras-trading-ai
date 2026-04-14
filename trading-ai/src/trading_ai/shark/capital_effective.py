"""Effective trading capital per outlet (API vs env fallbacks)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _kalshi_cash_reserve_pct() -> float:
    """Fraction of available Kalshi cash held back as reserve (default 20%)."""
    raw = (os.environ.get("KALSHI_CASH_RESERVE_PCT") or "0.20").strip()
    try:
        v = float(raw)
        return max(0.0, min(0.95, v))
    except ValueError:
        return 0.20


def _apply_kalshi_reserve(balance: float) -> float:
    """Deduct cash reserve from ``balance``; log the split. Returns deployable amount."""
    pct = _kalshi_cash_reserve_pct()
    if pct <= 0.0:
        return float(balance)
    reserve = balance * pct
    deployable = balance - reserve
    logger.info(
        "Kalshi reserve: keeping $%.2f (%.0f%%) — deploying from $%.2f",
        reserve,
        pct * 100,
        deployable,
    )
    return max(0.0, deployable)


def effective_capital_for_outlet(outlet: str, book_capital: float) -> float:
    """
    Kalshi: use live API ``available`` balance when it is > 0, then deduct cash reserve.

    ``KALSHI_ACTUAL_BALANCE`` applies only when the API reports about $0 available **and** there are
    open Kalshi positions (legacy workaround). Otherwise the API value is trusted.

    A 20% cash reserve (``KALSHI_CASH_RESERVE_PCT``) is always held back so the bot never
    deploys more than 80% of available cash in a single cycle.
    """
    o = (outlet or "").strip().lower()
    if o != "kalshi":
        return float(book_capital)
    try:
        from trading_ai.shark.balance_sync import fetch_kalshi_balance_usd
        from trading_ai.shark.kalshi_limits import should_apply_kalshi_actual_balance_override

        api = fetch_kalshi_balance_usd()
    except Exception as exc:
        logger.debug("Kalshi balance fetch skipped: %s", exc)
        api = None
    env_raw = (os.environ.get("KALSHI_ACTUAL_BALANCE") or "").strip()
    try:
        env_alt = float(env_raw) if env_raw else 0.0
    except ValueError:
        env_alt = 0.0
    if api is not None and api > 1e-6:
        return _apply_kalshi_reserve(float(api))
    if env_alt > 1e-6 and should_apply_kalshi_actual_balance_override(api):
        logger.info(
            "Kalshi effective capital: KALSHI_ACTUAL_BALANCE=$%.2f (API available was $%.2f, open positions present)",
            env_alt,
            api,
        )
        return _apply_kalshi_reserve(env_alt)
    if api is not None:
        return _apply_kalshi_reserve(float(api))
    return _apply_kalshi_reserve(float(book_capital))
