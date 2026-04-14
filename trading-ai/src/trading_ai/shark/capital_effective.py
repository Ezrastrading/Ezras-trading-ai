"""Effective trading capital per outlet (API vs env fallbacks)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def effective_capital_for_outlet(outlet: str, book_capital: float) -> float:
    """
    Kalshi: use live API balance when > 0; if API is None or ~0, use ``KALSHI_ACTUAL_BALANCE``.
    Other outlets: ``book_capital`` (from capital.json).
    """
    o = (outlet or "").strip().lower()
    if o != "kalshi":
        return float(book_capital)
    try:
        from trading_ai.shark.balance_sync import fetch_kalshi_balance_usd

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
        return float(api)
    if env_alt > 1e-6:
        logger.info(
            "Kalshi: using KALSHI_ACTUAL_BALANCE=$%.2f (API was %s)",
            env_alt,
            api,
        )
        return env_alt
    return float(book_capital)
