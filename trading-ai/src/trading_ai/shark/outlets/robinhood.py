"""Robinhood — optional ``robin_stocks`` integration (stocks/options; no import-time login)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.outlets.base import BaseOutletFetcher

load_shark_dotenv()

logger = logging.getLogger(__name__)

_rh_logged_in = False


def _ensure_robinhood_login() -> bool:
    global _rh_logged_in
    if _rh_logged_in:
        return True
    user = (os.environ.get("ROBINHOOD_USERNAME") or "").strip()
    pw = (os.environ.get("ROBINHOOD_PASSWORD") or "").strip()
    if not user or not pw:
        return False
    try:
        import robin_stocks.robinhood as r  # type: ignore

        mfa = (os.environ.get("ROBINHOOD_MFA_CODE") or "").strip() or None
        r.login(username=user, password=pw, mfa_code=mfa)
        _rh_logged_in = True
        return True
    except Exception as exc:
        logger.warning("Robinhood login failed: %s", exc)
        return False


class RobinhoodFetcher(BaseOutletFetcher):
    outlet_name = "robinhood"

    def fetch_binary_markets(self) -> list[MarketSnapshot]:
        return []

    @staticmethod
    def get_stock_price(symbol: str) -> float:
        if not _ensure_robinhood_login():
            return 0.0
        try:
            import robin_stocks.robinhood as r  # type: ignore

            q = r.get_latest_price(symbol.upper())
            if isinstance(q, list) and q:
                return float(q[0])
            if isinstance(q, str):
                return float(q)
            return float(q or 0.0)
        except Exception as exc:
            logger.warning("Robinhood get_stock_price %s: %s", symbol, exc)
            return 0.0

    @staticmethod
    def get_portfolio() -> Dict[str, Any]:
        if not _ensure_robinhood_login():
            return {"ok": False, "error": "not_configured"}
        try:
            import robin_stocks.robinhood as r  # type: ignore

            pos = r.get_open_stock_positions() or []
            profile = r.load_portfolio_profile() or {}
            return {"ok": True, "positions": pos, "profile": profile}
        except Exception as exc:
            logger.warning("Robinhood get_portfolio: %s", exc)
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def buy_market(symbol: str, shares: float) -> Dict[str, Any]:
        if not _ensure_robinhood_login():
            return {"ok": False, "error": "not_configured"}
        try:
            import robin_stocks.robinhood as r  # type: ignore

            out = r.order_buy_market(symbol.upper(), float(shares))
            return {"ok": True, "raw": out}
        except Exception as exc:
            logger.warning("Robinhood buy_market: %s", exc)
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def sell_market(symbol: str, shares: float) -> Dict[str, Any]:
        if not _ensure_robinhood_login():
            return {"ok": False, "error": "not_configured"}
        try:
            import robin_stocks.robinhood as r  # type: ignore

            out = r.order_sell_market(symbol.upper(), float(shares))
            return {"ok": True, "raw": out}
        except Exception as exc:
            logger.warning("Robinhood sell_market: %s", exc)
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def get_options_chain(symbol: str) -> Dict[str, Any]:
        if not _ensure_robinhood_login():
            return {"ok": False, "error": "not_configured"}
        try:
            import robin_stocks.robinhood as r  # type: ignore

            chain = r.get_chains(symbol.upper())
            return {"ok": True, "chain": chain}
        except Exception as exc:
            logger.warning("Robinhood get_options_chain: %s", exc)
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def buy_option(
        symbol: str,
        expiry: str,
        strike: str,
        option_type: str,
        quantity: int,
    ) -> Dict[str, Any]:
        """
        Options orders require instrument URLs from Robinhood's chain API.
        This hook is intentionally conservative until a venue-specific order builder is wired.
        """
        _ = expiry, strike, quantity
        if not _ensure_robinhood_login():
            return {"ok": False, "error": "not_configured"}
        ot = option_type.strip().lower()
        if ot not in ("call", "put"):
            return {"ok": False, "error": "option_type must be call or put"}
        return {
            "ok": False,
            "error": "option_order_builder_not_wired",
            "symbol": symbol.upper(),
            "option_type": ot,
        }
