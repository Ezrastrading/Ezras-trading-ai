"""Coinbase Advanced Trade — spot prices, balances, optional orders (treasury venue)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.outlets.base import BaseOutletFetcher

load_shark_dotenv()

logger = logging.getLogger(__name__)

_PUBLIC_SPOT = "https://api.coinbase.com/v2/prices/{product_id}/spot"


def _public_spot(product_id: str) -> Optional[float]:
    url = _PUBLIC_SPOT.format(product_id=urllib.parse.quote(product_id, safe=""))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark/1.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        amt = body.get("data", {}).get("amount")
        return float(amt) if amt is not None else None
    except Exception as exc:
        logger.debug("Coinbase public spot %s: %s", product_id, exc)
        return None


class CoinbaseFetcher(BaseOutletFetcher):
    """Crypto outlet — no binary markets in the shark scanner; prices feed strategies + treasury."""

    outlet_name = "coinbase"

    def fetch_binary_markets(self) -> list[MarketSnapshot]:
        return []

    @staticmethod
    def fetch_crypto_prices() -> Dict[str, float]:
        """Spot USD for core products (public endpoint; no API keys)."""
        products = ("BTC-USD", "ETH-USD", "SOL-USD", "MATIC-USD")
        out: Dict[str, float] = {}
        for pid in products:
            p = _public_spot(pid)
            if p is not None:
                out[pid] = round(p, 6 if "BTC" in pid else 4)
        return out

    @staticmethod
    def fetch_portfolio() -> Dict[str, Any]:
        """Brokerage balances when ``COINBASE_API_KEY`` + ``COINBASE_API_SECRET`` are set."""
        try:
            from trading_ai.shark.coinbase_tracker import get_coinbase_balance

            return dict(get_coinbase_balance())
        except Exception as exc:
            logger.warning("Coinbase portfolio fetch: %s", exc)
            return {"usdc": 0.0, "eth_qty": 0.0, "eth_usd_value": 0.0, "error": str(exc)}

    @staticmethod
    def _rest_client():
        key = (os.environ.get("COINBASE_API_KEY") or "").strip()
        sec = (os.environ.get("COINBASE_API_SECRET") or "").strip()
        if not key or not sec:
            return None
        try:
            from coinbase.rest import RESTClient

            return RESTClient(api_key=key, api_secret=sec)
        except ImportError:
            logger.debug("coinbase-advanced-py not installed — order methods unavailable")
            return None
        except Exception as exc:
            logger.warning("Coinbase RESTClient init failed: %s", exc)
            return None

    @classmethod
    def place_market_order(cls, product_id: str, side: str, size: str) -> Dict[str, Any]:
        client = cls._rest_client()
        if client is None:
            return {"ok": False, "error": "coinbase_client_unavailable"}
        try:
            oid = f"ezras-{product_id}-{side}-{size}"[:128]
            side_l = side.strip().lower()
            if side_l == "buy":
                r = client.market_order_buy(client_order_id=oid, product_id=product_id, base_size=str(size))
            elif side_l == "sell":
                r = client.market_order_sell(client_order_id=oid, product_id=product_id, base_size=str(size))
            else:
                return {"ok": False, "error": "invalid_side"}
            return {"ok": True, "raw": r.to_dict() if hasattr(r, "to_dict") else str(r)}
        except Exception as exc:
            logger.warning("Coinbase market order failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    @classmethod
    def place_limit_order(cls, product_id: str, side: str, size: str, price: str) -> Dict[str, Any]:
        client = cls._rest_client()
        if client is None:
            return {"ok": False, "error": "coinbase_client_unavailable"}
        try:
            oid = f"ezras-lmt-{product_id}"[:120]
            side_l = side.strip().lower()
            if side_l == "buy":
                r = client.limit_order_gtc_buy(
                    client_order_id=oid,
                    product_id=product_id,
                    base_size=str(size),
                    limit_price=str(price),
                )
            elif side_l == "sell":
                r = client.limit_order_gtc_sell(
                    client_order_id=oid,
                    product_id=product_id,
                    base_size=str(size),
                    limit_price=str(price),
                )
            else:
                return {"ok": False, "error": "invalid_side"}
            return {"ok": True, "raw": r.to_dict() if hasattr(r, "to_dict") else str(r)}
        except Exception as exc:
            logger.warning("Coinbase limit order failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def get_balance(currency: str) -> float:
        """USDC / ETH from brokerage snapshot; extend when multi-asset ledger is needed."""
        cur = currency.strip().upper()
        p = CoinbaseFetcher.fetch_portfolio()
        if cur == "USDC":
            return float(p.get("usdc") or 0.0)
        if cur == "ETH":
            return float(p.get("eth_qty") or 0.0)
        logger.debug("Coinbase get_balance: unsupported currency %s", cur)
        return 0.0
