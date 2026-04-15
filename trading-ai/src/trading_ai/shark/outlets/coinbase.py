"""
Coinbase outlet — two layers:

  CoinbaseFetcher  Legacy outlet fetcher (public prices, treasury, optional SDK orders).
  CoinbaseClient   Advanced Trade API v3 client — ES256 JWT auth, used by the
                   24/7 accumulator (coinbase_accumulator.py).

Advanced Trade auth env vars:
  COINBASE_API_KEY_NAME    organizations/{org_id}/apiKeys/{key_id}
  COINBASE_API_PRIVATE_KEY EC PEM private key (P-256 / ES256)
                           Literal \\n escapes from Railway / .env are normalised.

Base URL: https://api.coinbase.com/api/v3/brokerage
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from urllib.parse import urlparse, urlencode
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot, OrderResult
from trading_ai.shark.outlets.base import BaseOutletFetcher

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[misc, assignment]

load_shark_dotenv()
logger = logging.getLogger(__name__)

# ── Advanced Trade constants ──────────────────────────────────────────────────

_ADV_BASE_URL = "https://api.coinbase.com/api/v3/brokerage"
_JWT_HOST = "api.coinbase.com"

# ── Legacy public price constant ─────────────────────────────────────────────

_PUBLIC_SPOT = "https://api.coinbase.com/v2/prices/{product_id}/spot"


# ── helpers ───────────────────────────────────────────────────────────────────


def normalize_coinbase_key_material(raw: str) -> str:
    """Turn .env / Railway single-line PEM (with ``\\n`` escapes) into real PEM."""
    if not raw:
        return raw
    return raw.replace("\\n", "\n").strip()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _query_string(params: Optional[Dict[str, Any]]) -> str:
    """
    Build ``?a=1&b=2`` for JWT ``uri`` and the HTTP URL.

    Uses :func:`urllib.parse.urlencode` so product ids like ``BTC-USD`` stay
    unencoded (hyphens). Over-aggressive :func:`~urllib.parse.quote` with
    ``safe=\"\"`` turns ``-`` into ``%2D``, which can break CDP JWT checks for
    ``/best_bid_ask?product_ids=...``.
    """
    if not params:
        return ""
    pairs: List[Tuple[str, str]] = []
    for k, v in params.items():
        if v is None:
            continue
        ks = str(k)
        if isinstance(v, list):
            for item in v:
                pairs.append((ks, str(item)))
        else:
            pairs.append((ks, str(v)))
    return "?" + urlencode(pairs)


def _get_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception as exc:
            if isinstance(exc, (OSError, MemoryError)):
                logger.debug("Coinbase: certifi context skipped, using default SSL")
            else:
                logger.warning(
                    "Coinbase: certifi context failed (%s), using default",
                    type(exc).__name__,
                )
    return ssl.create_default_context()


def _public_spot(product_id: str) -> Optional[float]:
    url = _PUBLIC_SPOT.format(product_id=urllib.parse.quote(product_id, safe=""))
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "EzrasShark/1.0"}, method="GET"
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        amt = body.get("data", {}).get("amount")
        return float(amt) if amt is not None else None
    except Exception as exc:
        logger.debug("Coinbase public spot %s: %s", product_id, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CoinbaseAuthError
# ─────────────────────────────────────────────────────────────────────────────


class CoinbaseAuthError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# CoinbaseClient  — Advanced Trade API v3
# ─────────────────────────────────────────────────────────────────────────────


class CoinbaseClient:
    """
    Coinbase Advanced Trade API v3 HTTP client.

    Each request is authenticated with a fresh ES256 JWT (120 s TTL) generated
    from ``COINBASE_API_KEY_NAME`` (key name) and ``COINBASE_API_PRIVATE_KEY``
    (P-256 EC PEM).  No third-party JWT library required — uses ``cryptography``.
    """

    def __init__(
        self,
        api_key_name: Optional[str] = None,
        private_key_pem: Optional[str] = None,
        base_url: str = _ADV_BASE_URL,
    ) -> None:
        self._key_name = (
            api_key_name or os.environ.get("COINBASE_API_KEY_NAME") or ""
        ).strip()
        raw_pem = private_key_pem or os.environ.get("COINBASE_API_PRIVATE_KEY") or ""
        self._pem = normalize_coinbase_key_material(raw_pem)
        self.base_url = base_url.rstrip("/")
        self._private_key: Optional[Any] = None
        self.last_error: Optional[str] = None
        self.marked_down: bool = False

        if self._pem:
            try:
                from cryptography.hazmat.primitives.serialization import (
                    load_pem_private_key,
                )

                self._private_key = load_pem_private_key(
                    self._pem.encode("utf-8"), password=None
                )
                logger.info("Coinbase Advanced Trade EC key loaded OK")
            except Exception as exc:
                logger.warning("Coinbase: could not load PEM private key: %s", exc)

    def has_credentials(self) -> bool:
        return bool(self._key_name and self._private_key)

    # ── JWT ───────────────────────────────────────────────────────────────────

    def _build_jwt(self, method: str, request_path: str) -> str:
        """
        Build an ES256 JWT per the Coinbase Advanced Trade API v3 specification.

        Header:  ``{"alg":"ES256","kid":"<key_name>","nonce":"<32-hex>"}``
                  (``nonce`` is required by CDP; 16 random bytes as hex.)
        Payload: ``{"sub":"<key_name>","iss":"cdp","nbf":<now>,"exp":<now+120>,
                    "uri":"<METHOD> api.coinbase.com<full-path>"}``
        ``request_path`` must be the **full path on the host**, including
        ``/api/v3/brokerage``, and for GET requests **including the query string**
        (e.g. ``/api/v3/brokerage/accounts`` or
        ``/api/v3/brokerage/best_bid_ask?product_ids=BTC-USD``).

        Signature: ECDSA-P256-SHA256 over r‖s (32 B each), base64url (no padding).
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        now = int(time.time())
        header: Dict[str, Any] = {
            "alg": "ES256",
            "kid": self._key_name,
            "nonce": secrets.token_hex(16),
        }
        payload: Dict[str, Any] = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": f"{method.upper()} {_JWT_HOST}{request_path}",
        }
        h64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        p64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h64}.{p64}".encode()

        raw_sig = self._private_key.sign(  # type: ignore[union-attr]
            signing_input, ec.ECDSA(hashes.SHA256())
        )
        r, s = decode_dss_signature(raw_sig)
        sig64 = _b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        jwt_token = f"{h64}.{p64}.{sig64}"

        # Debug (no signature): payload only. Enable with COINBASE_DEBUG_JWT=1.
        if (os.environ.get("COINBASE_DEBUG_JWT") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            try:
                payload_b64 = jwt_token.split(".")[1]
                pad = (-len(payload_b64)) % 4
                payload_b64 += "=" * pad
                decoded = json.loads(base64.urlsafe_b64decode(payload_b64))
                logger.info("Coinbase JWT payload: %s", decoded)
            except Exception as exc:
                logger.warning("Coinbase JWT payload decode failed: %s", exc)

        return jwt_token

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        _retry_5xx: int = 0,
    ) -> Dict[str, Any]:
        if self.marked_down:
            raise RuntimeError("coinbase outlet marked down")
        if not self.has_credentials():
            raise CoinbaseAuthError("Coinbase credentials not configured")

        # Build query string first — JWT `uri` claim must match the full request path + query.
        qs = _query_string(params)

        base_path = urlparse(self.base_url).path.rstrip("/")
        request_path = f"{base_path}{path}{qs}"
        jwt_token = self._build_jwt(method, request_path)
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        url = f"{self.base_url}{path}{qs}"
        data: Optional[bytes] = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        ssl_ctx = _get_ssl_context()

        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {}
            out = json.loads(raw)
            return out if isinstance(out, dict) else {"_data": out}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.last_error = "401"
                raise CoinbaseAuthError("401 unauthorized") from e
            if e.code == 429:
                logger.warning("Coinbase 429 — backing off 10s")
                time.sleep(10)
                return self._request(method, path, params=params, body=body)
            if 500 <= e.code < 600 and _retry_5xx < 3:
                delay = (2**_retry_5xx) * 0.5
                logger.warning(
                    "Coinbase %s — retry %s after %.1fs",
                    e.code,
                    _retry_5xx + 1,
                    delay,
                )
                time.sleep(delay)
                return self._request(
                    method, path, params=params, body=body, _retry_5xx=_retry_5xx + 1
                )
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = str(e)
            raise RuntimeError(f"Coinbase HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            logger.error("Coinbase network error: %s", e)
            self.marked_down = True
            self.last_error = str(e)
            raise

    # ── account ───────────────────────────────────────────────────────────────

    def get_accounts(self) -> List[Dict[str, Any]]:
        """GET /accounts — list all portfolio accounts with balances."""
        j = self._request("GET", "/accounts")
        return j.get("accounts") or []

    def get_usd_balance(self) -> float:
        """Available USD balance in the connected Coinbase account."""
        return self.get_available_balance("USD")

    def get_available_balance(self, currency: str) -> float:
        """Available balance for a currency code (e.g. ``USD``, ``BTC``, ``ETH``)."""
        try:
            cur = (currency or "").strip().upper()
            for acct in self.get_accounts():
                if str(acct.get("currency") or "").upper() == cur:
                    avail = acct.get("available_balance") or {}
                    return float(avail.get("value") or 0.0)
        except Exception as exc:
            logger.warning("Coinbase %s balance fetch failed: %s", currency, exc)
        return 0.0

    # ── prices ────────────────────────────────────────────────────────────────

    def get_price(self, product_id: str) -> Tuple[float, float]:
        """Return (bid, ask) for a single product_id."""
        prices = self.get_prices([product_id])
        return prices.get(product_id, (0.0, 0.0))

    def get_prices(self, product_ids: List[str]) -> Dict[str, Tuple[float, float]]:
        """
        GET /best_bid_ask — return ``{product_id: (bid, ask)}`` for multiple products.

        Uses repeated ``product_ids`` query params as required by the v3 API.
        """
        if not product_ids:
            return {}
        j = self._request(
            "GET", "/best_bid_ask", params={"product_ids": product_ids}
        )
        result: Dict[str, Tuple[float, float]] = {}
        for book in j.get("pricebooks") or []:
            pid = str(book.get("product_id") or "")
            if not pid:
                continue
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            bid = float(bids[0]["price"]) if bids else 0.0
            ask = float(asks[0]["price"]) if asks else 0.0
            result[pid] = (bid, ask)
        return result

    # ── orders ────────────────────────────────────────────────────────────────

    def place_market_buy(self, product_id: str, usd_amount: float) -> OrderResult:
        """
        Market buy ``usd_amount`` USD worth of ``product_id``.
        Uses ``market_market_ioc`` with ``quote_size`` (USD spend).
        """
        client_order_id = uuid.uuid4().hex
        body = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": "BUY",
            "order_configuration": {
                "market_market_ioc": {"quote_size": f"{usd_amount:.2f}"}
            },
        }
        try:
            j = self._request("POST", "/orders", body=body)
            success = bool(j.get("success"))
            sr = j.get("success_response") or {}
            order_id = str(sr.get("order_id") or j.get("order_id") or client_order_id)
            if not success:
                err = j.get("error_response") or {}
                reason = str(err.get("message") or err.get("error") or "order failed")
                logger.warning("Coinbase BUY failed (%s): %s", product_id, reason)
                return OrderResult(
                    order_id=client_order_id,
                    filled_price=0.0,
                    filled_size=0.0,
                    timestamp=time.time(),
                    status="error",
                    outlet="coinbase",
                    success=False,
                    reason=reason,
                    raw=j,
                )
            logger.info(
                "Coinbase BUY placed: %s $%.2f → order_id=%s",
                product_id,
                usd_amount,
                order_id,
            )
            return OrderResult(
                order_id=order_id,
                filled_price=0.0,  # exact fill resolved via get_fills
                filled_size=0.0,
                timestamp=time.time(),
                status="placed",
                outlet="coinbase",
                success=True,
                raw=j,
            )
        except Exception as exc:
            logger.error("Coinbase BUY exception (%s): %s", product_id, exc)
            return OrderResult(
                order_id=client_order_id,
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="error",
                outlet="coinbase",
                success=False,
                reason=str(exc),
                raw={},
            )

    def place_market_sell(self, product_id: str, base_size: str) -> OrderResult:
        """
        Market sell ``base_size`` units of ``product_id`` (e.g. '0.00135' BTC).
        Uses ``market_market_ioc`` with ``base_size`` (asset quantity).
        """
        client_order_id = uuid.uuid4().hex
        body = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": "SELL",
            "order_configuration": {
                "market_market_ioc": {"base_size": str(base_size)}
            },
        }
        try:
            j = self._request("POST", "/orders", body=body)
            success = bool(j.get("success"))
            sr = j.get("success_response") or {}
            order_id = str(sr.get("order_id") or j.get("order_id") or client_order_id)
            if not success:
                err = j.get("error_response") or {}
                reason = str(err.get("message") or err.get("error") or "order failed")
                logger.warning("Coinbase SELL failed (%s): %s", product_id, reason)
                return OrderResult(
                    order_id=client_order_id,
                    filled_price=0.0,
                    filled_size=0.0,
                    timestamp=time.time(),
                    status="error",
                    outlet="coinbase",
                    success=False,
                    reason=reason,
                    raw=j,
                )
            logger.info(
                "Coinbase SELL placed: %s %s units → order_id=%s",
                product_id,
                base_size,
                order_id,
            )
            return OrderResult(
                order_id=order_id,
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="placed",
                outlet="coinbase",
                success=True,
                raw=j,
            )
        except Exception as exc:
            logger.error("Coinbase SELL exception (%s): %s", product_id, exc)
            return OrderResult(
                order_id=client_order_id,
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="error",
                outlet="coinbase",
                success=False,
                reason=str(exc),
                raw={},
            )

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """GET /orders/historical/{order_id} — poll order status / fill details."""
        safe_id = urllib.parse.quote(order_id, safe="")
        j = self._request("GET", f"/orders/historical/{safe_id}")
        return j.get("order") or j

    def cancel_order(self, order_id: str) -> bool:
        """POST /orders/batch_cancel — cancel a single open order."""
        try:
            j = self._request(
                "POST", "/orders/batch_cancel", body={"order_ids": [order_id]}
            )
            results = j.get("results") or []
            if results:
                return bool(results[0].get("success"))
            return bool(j.get("success"))
        except Exception as exc:
            logger.warning("Coinbase cancel_order failed (%s): %s", order_id, exc)
            return False

    def get_fills(self, order_id: str) -> List[Dict[str, Any]]:
        """GET /orders/historical/fills?order_id=xxx — trade executions for an order."""
        j = self._request(
            "GET", "/orders/historical/fills", params={"order_id": order_id}
        )
        return j.get("fills") or []


# ─────────────────────────────────────────────────────────────────────────────
# CoinbaseFetcher  — legacy outlet / treasury helper (unchanged)
# ─────────────────────────────────────────────────────────────────────────────


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
                r = client.market_order_buy(
                    client_order_id=oid, product_id=product_id, base_size=str(size)
                )
            elif side_l == "sell":
                r = client.market_order_sell(
                    client_order_id=oid, product_id=product_id, base_size=str(size)
                )
            else:
                return {"ok": False, "error": "invalid_side"}
            return {"ok": True, "raw": r.to_dict() if hasattr(r, "to_dict") else str(r)}
        except Exception as exc:
            logger.warning("Coinbase market order failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    @classmethod
    def place_limit_order(
        cls, product_id: str, side: str, size: str, price: str
    ) -> Dict[str, Any]:
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
        """USDC / ETH from brokerage snapshot."""
        cur = currency.strip().upper()
        p = CoinbaseFetcher.fetch_portfolio()
        if cur == "USDC":
            return float(p.get("usdc") or 0.0)
        if cur == "ETH":
            return float(p.get("eth_qty") or 0.0)
        logger.debug("Coinbase get_balance: unsupported currency %s", cur)
        return 0.0
