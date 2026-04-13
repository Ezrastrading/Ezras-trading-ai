"""
Coinbase Advanced Trade tracker — USDC + ETH balance monitoring.

The master treasury lives in Coinbase:
  - USDC_TARGET_PCT % in USDC (default 60%)
  - ETH_TARGET_PCT  % in ETH  (default 40%)

Operator withdraws profits from Kalshi/Manifold/options platforms
manually and sends to their Coinbase wallet.

Credentials (all optional — system functions without them):
  COINBASE_API_KEY
  COINBASE_API_SECRET

Docs: https://docs.cdp.coinbase.com/advanced-trade/reference
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)

_BASE = "https://api.coinbase.com/api/v3/brokerage"


def _sign_request(api_key: str, api_secret: str, method: str, path: str, body: str = "") -> Dict[str, str]:
    """Generate Coinbase Advanced Trade API v3 JWT-style HMAC headers."""
    timestamp = str(int(time.time()))
    message = timestamp + method.upper() + path + body
    signature = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "CB-ACCESS-KEY": api_key,
        "CB-ACCESS-SIGN": signature,
        "CB-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
        "User-Agent": "EzrasTreasury/1.0",
    }


def get_coinbase_balance() -> Dict[str, Any]:
    """
    Fetch USDC and ETH balances from Coinbase.
    Returns dict with keys: usdc, eth_qty, eth_usd_value, last_updated.
    Returns zeros gracefully if no credentials or request fails.
    """
    api_key = (os.environ.get("COINBASE_API_KEY") or "").strip()
    api_secret = (os.environ.get("COINBASE_API_SECRET") or "").strip()

    default = {
        "usdc": 0.0,
        "eth_qty": 0.0,
        "eth_usd_value": 0.0,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "source": "no_credentials" if not (api_key and api_secret) else "fetch_failed",
    }

    if not (api_key and api_secret):
        return default

    try:
        path = "/accounts"
        headers = _sign_request(api_key, api_secret, "GET", path)
        req = urllib.request.Request(
            f"{_BASE}/accounts",
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())

        usdc = 0.0
        eth_qty = 0.0

        for acct in body.get("accounts", []):
            currency = acct.get("currency", "")
            balance = float(acct.get("available_balance", {}).get("value", 0))
            if currency == "USDC":
                usdc = round(balance, 2)
            elif currency == "ETH":
                eth_qty = round(balance, 6)

        # Approximate ETH USD value (would need price feed for precision)
        eth_usd = eth_qty * _get_eth_price_usd()

        return {
            "usdc": usdc,
            "eth_qty": eth_qty,
            "eth_usd_value": round(eth_usd, 2),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "coinbase_api",
        }

    except urllib.error.HTTPError as e:
        logger.warning("Coinbase balance HTTP %s: %s", e.code, e.reason)
        default["source"] = f"http_error_{e.code}"
        return default
    except Exception as exc:
        logger.warning("Coinbase balance error: %s", exc)
        return default


def _get_eth_price_usd() -> float:
    """Fetch ETH/USD spot price. Returns 0 on failure."""
    try:
        req = urllib.request.Request(
            "https://api.coinbase.com/v2/prices/ETH-USD/spot",
            headers={"User-Agent": "EzrasTreasury/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        return float(body.get("data", {}).get("amount", 0))
    except Exception:
        return 0.0


def track_withdrawal_to_coinbase(amount_usd: float, asset: str = "USDC") -> None:
    """
    Log that the operator withdrew funds to Coinbase.
    Updates treasury state and logs the event.
    """
    try:
        from trading_ai.shark.treasury import log_withdrawal
        log_withdrawal(amount_usd)
        logger.info(
            "Coinbase withdrawal logged: $%.2f → %s",
            amount_usd, asset.upper()
        )
    except Exception as exc:
        logger.warning("Failed to log Coinbase withdrawal: %s", exc)
