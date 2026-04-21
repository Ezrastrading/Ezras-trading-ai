"""
Webull options client — long options ONLY (defined risk).

Mirrors Tastytrade strategy but uses different strikes/expiries
for diversification. Run both platforms in parallel.

Auth: WEBULL_API_KEY + WEBULL_ACCOUNT_ID from .env
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)

_BASE = "https://openapi.webull.com"
_MAX_POSITION_PCT = 0.40   # never exceed 40% of account in one position


class WebullClient:
    """Webull API client — defined-risk long options only."""

    def __init__(self) -> None:
        self.api_key = (os.environ.get("WEBULL_API_KEY") or "").strip()
        self.account_id = (os.environ.get("WEBULL_ACCOUNT_ID") or "").strip()

    def has_credentials(self) -> bool:
        return bool(self.api_key and self.account_id)

    def _auth_headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {
            "User-Agent": "EzrasTrade/1.0",
            "Accept": "application/json",
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def get_account_balance(self) -> Optional[float]:
        """GET /account/info → net account value in USD."""
        if not self.has_credentials():
            logger.debug("Webull: no credentials configured")
            return None
        try:
            url = f"{_BASE}/account/info?account_id={self.account_id}"
            req = urllib.request.Request(url, headers=self._auth_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            nlv = body.get("data", {}).get("net_liquidation_value")
            return round(float(nlv), 2) if nlv is not None else None
        except Exception as exc:
            logger.warning("Webull balance error: %s", exc)
            return None

    def get_positions(self) -> List[Dict[str, Any]]:
        """GET /account/positions"""
        if not self.has_credentials():
            return []
        try:
            url = f"{_BASE}/account/positions?account_id={self.account_id}"
            req = urllib.request.Request(url, headers=self._auth_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            return body.get("data", {}).get("positions", [])
        except Exception as exc:
            logger.warning("Webull positions error: %s", exc)
            return []

    def validate_defined_risk(
        self, option_side: str, account_balance: float, stake_usd: float
    ) -> bool:
        """
        Enforce defined-risk constraint:
        - option_side must be 'BUY_CALL' or 'BUY_PUT' — long only
        - stake_usd must not exceed MAX_POSITION_PCT of account_balance
        """
        allowed = {"BUY_CALL", "BUY_PUT", "CALL", "PUT", "C", "P"}
        if option_side.upper() not in allowed:
            logger.warning("Webull: rejected non-long-option side: %s", option_side)
            return False
        max_stake = account_balance * _MAX_POSITION_PCT
        if stake_usd > max_stake:
            logger.warning(
                "Webull: position $%.2f exceeds 40%% limit ($%.2f); rejected",
                stake_usd, max_stake,
            )
            return False
        return True

    def place_order(
        self,
        ticker: str,
        option_side: str,   # "BUY_CALL" or "BUY_PUT"
        strike: float,
        expiry: str,        # YYYY-MM-DD
        quantity: int,
        limit_price: float,
        account_balance: float,
    ) -> Dict[str, Any]:
        """
        Place a long option order. Returns result dict.
        Rejects if defined-risk constraints are violated.
        """
        stake = limit_price * quantity * 100
        if not self.validate_defined_risk(option_side, account_balance, stake):
            return {"ok": False, "error": "defined_risk_constraint_violated"}
        if not self.has_credentials():
            return {"ok": False, "error": "no_credentials"}
        try:
            order_payload = {
                "account_id": self.account_id,
                "ticker": ticker,
                "action": option_side.upper(),
                "strike": str(strike),
                "expiry": expiry,
                "quantity": quantity,
                "order_type": "LMT",
                "limit_price": str(limit_price),
                "time_in_force": "DAY",
            }
            payload = json.dumps(order_payload).encode()
            url = f"{_BASE}/options/order"
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
            logger.warning("Webull order error: %s", exc)
            return {"ok": False, "error": str(exc)}
