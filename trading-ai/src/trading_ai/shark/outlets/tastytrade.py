"""
Tastytrade options client — long options ONLY (defined risk).

Strategy for $25 account:
- Long calls/puts only; never sell naked options
- Max position: 40% of account per trade
- Target: binary events with known resolution dates
  (earnings, Fed decisions, economic data releases)
- Mirrors prediction-market logic in options format

Docs: https://developer.tastytrade.com
Auth: username + password → session token (POST /sessions)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot

load_shark_dotenv()

logger = logging.getLogger(__name__)

_BASE = "https://api.tastytrade.com"
_MAX_POSITION_PCT = 0.40   # never exceed 40% of account in one position


class TastytradeClient:
    """Tastytrade API client — defined-risk long options only."""

    def __init__(self) -> None:
        self.username = (os.environ.get("TASTYTRADE_USERNAME") or "").strip()
        self.password = (os.environ.get("TASTYTRADE_PASSWORD") or "").strip()
        self._session_token: Optional[str] = None
        self._account_number: Optional[str] = None

    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    def authenticate(self) -> bool:
        """POST /sessions → session token. Returns True on success."""
        if not self.has_credentials():
            logger.debug("Tastytrade: no credentials configured")
            return False
        try:
            payload = json.dumps({"login": self.username, "password": self.password}).encode()
            req = urllib.request.Request(
                f"{_BASE}/sessions",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "EzrasTrade/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            self._session_token = body.get("data", {}).get("session-token")
            return bool(self._session_token)
        except Exception as exc:
            logger.warning("Tastytrade auth error: %s", exc)
            return False

    def _auth_headers(self) -> Dict[str, str]:
        h = {"User-Agent": "EzrasTrade/1.0", "Accept": "application/json"}
        if self._session_token:
            h["Authorization"] = self._session_token
        return h

    def get_accounts(self) -> List[Dict[str, Any]]:
        """GET /customers/me/accounts"""
        if not self._session_token:
            return []
        try:
            req = urllib.request.Request(
                f"{_BASE}/customers/me/accounts",
                headers=self._auth_headers(),
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            accounts = body.get("data", {}).get("items", [])
            if accounts and not self._account_number:
                self._account_number = accounts[0].get("account", {}).get("account-number")
            return accounts
        except Exception as exc:
            logger.warning("Tastytrade get_accounts error: %s", exc)
            return []

    def get_account_balance(self) -> Optional[float]:
        """GET /accounts/{account_number}/balances → net liquidating value in USD."""
        if not self._session_token or not self._account_number:
            return None
        try:
            url = f"{_BASE}/accounts/{self._account_number}/balances"
            req = urllib.request.Request(url, headers=self._auth_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            nlv = body.get("data", {}).get("net-liquidating-value")
            return round(float(nlv), 2) if nlv is not None else None
        except Exception as exc:
            logger.warning("Tastytrade balance error: %s", exc)
            return None

    def get_positions(self) -> List[Dict[str, Any]]:
        """GET /accounts/{account_number}/positions"""
        if not self._session_token or not self._account_number:
            return []
        try:
            url = f"{_BASE}/accounts/{self._account_number}/positions"
            req = urllib.request.Request(url, headers=self._auth_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            return body.get("data", {}).get("items", [])
        except Exception as exc:
            logger.warning("Tastytrade positions error: %s", exc)
            return []

    def validate_defined_risk(
        self, option_type: str, account_balance: float, stake_usd: float
    ) -> bool:
        """
        Enforce defined-risk constraint:
        - option_type must be 'C' (call) or 'P' (put) — long only
        - stake_usd must not exceed MAX_POSITION_PCT of account_balance
        """
        if option_type.upper() not in ("C", "P", "CALL", "PUT"):
            logger.warning("Tastytrade: rejected non-long-option type: %s", option_type)
            return False
        max_stake = account_balance * _MAX_POSITION_PCT
        if stake_usd > max_stake:
            logger.warning(
                "Tastytrade: position $%.2f exceeds 40%% limit ($%.2f); rejected",
                stake_usd, max_stake,
            )
            return False
        return True

    def place_order(
        self,
        symbol: str,
        option_type: str,   # "C" or "P"
        strike: float,
        expiry: str,        # YYYY-MM-DD
        quantity: int,
        price: float,
        account_balance: float,
    ) -> Dict[str, Any]:
        """
        Place a long option order. Returns result dict.
        Rejects if defined-risk constraints are violated.
        """
        stake = price * quantity * 100  # 1 contract = 100 shares
        if not self.validate_defined_risk(option_type, account_balance, stake):
            return {"ok": False, "error": "defined_risk_constraint_violated"}
        if not self._session_token or not self._account_number:
            return {"ok": False, "error": "not_authenticated"}
        try:
            order_payload = {
                "time-in-force": "Day",
                "order-type": "Limit",
                "price": str(price),
                "price-effect": "Debit",
                "legs": [{
                    "instrument-type": "Equity Option",
                    "symbol": f"{symbol} {expiry.replace('-','')}{'%08.3f' % strike}{option_type.upper()}",
                    "quantity": str(quantity),
                    "action": "Buy to Open",
                }],
            }
            payload = json.dumps(order_payload).encode()
            url = f"{_BASE}/accounts/{self._account_number}/orders"
            req = urllib.request.Request(
                url,
                data=payload,
                headers={**self._auth_headers(), "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            return {"ok": True, "order": body.get("data", {})}
        except Exception as exc:
            logger.warning("Tastytrade order error: %s", exc)
            return {"ok": False, "error": str(exc)}

    def get_underlying_last_price(self, symbol: str) -> Optional[float]:
        """Best-effort last trade for an equity (session must be authenticated)."""
        if not self._session_token:
            return None
        sym = symbol.strip().upper()
        try:
            url = f"{_BASE}/market-data/equities/{sym}/last"
            req = urllib.request.Request(url, headers=self._auth_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = json.loads(resp.read())
            last = body.get("data", {}).get("last")
            return float(last) if last is not None else None
        except Exception as exc:
            logger.debug("Tastytrade last price %s: %s", sym, exc)
            return None


class TastytradeFetcher:
    """Registered outlet for scheduler/scanner symmetry — options are not binary CLOB markets."""

    outlet_name = "tastytrade"

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        return []
