"""
Coinbase outlet — two layers:

  CoinbaseFetcher  Legacy outlet fetcher (public prices, treasury, optional SDK orders).
  CoinbaseClient   Advanced Trade API v3 client — ES256 JWT auth, used by
                   NexTrading Engine (``trading_ai.nte`` / ``coinbase_accumulator``).

Advanced Trade auth env vars (either name pair works):
  COINBASE_API_KEY_NAME or COINBASE_API_KEY
      organizations/{org_id}/apiKeys/{key_id}
  COINBASE_API_PRIVATE_KEY or COINBASE_API_SECRET
      EC PEM private key (P-256 / ES256); literal \\n escapes from Railway / .env are normalised.

Balance / portfolio:
  COINBASE_PORTFOLIO_ID   Retail portfolio UUID (fiat often only under ``/portfolios/.../balances``).
  COINBASE_BALANCE_OVERRIDE  Optional fixed USD float for tests (bypasses API).

Base URL (authenticated orders/accounts): https://api.coinbase.com/api/v3/brokerage

Public prices and product list use **unauthenticated** Advanced Trade **market** routes
(``GET .../brokerage/market/products``, ``GET .../brokerage/market/products/{id}/ticker``).
JWT is used only for ``/accounts`` and ``/orders``. Exchange REST remains a fallback for ticker.
"""

from __future__ import annotations

import base64
import contextvars
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
from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[misc, assignment]

load_shark_dotenv()
logger = logging.getLogger(__name__)

# Set True only while executing a public order/cancel method so raw ``_request(POST,/orders)`` cannot bypass guards.
_COINBASE_ORDER_GUARD: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "coinbase_order_guard", default=False
)

# ── Advanced Trade constants ──────────────────────────────────────────────────

_ADV_BASE_URL = "https://api.coinbase.com/api/v3/brokerage"
_JWT_HOST = "api.coinbase.com"
# Public Advanced Trade market data (no JWT) — use ``/market/products``, not ``/products`` (401).
_ADV_PUBLIC_BASE = "https://api.coinbase.com/api/v3/brokerage"
# Coinbase Exchange REST (classic) — fallback only; product ids match (e.g. BTC-USD).
_EXCHANGE_REST_URL = "https://api.exchange.coinbase.com"
# Retail consumer portfolio UUID (fiat often only appears here, not in per-asset ``/accounts`` rows).
KNOWN_PORTFOLIO_ID = "aa4c900f-3580-56ad-930a-a1555233a476"

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


def _exchange_public_request(path: str) -> Any:
    """GET path on Coinbase Exchange REST (e.g. ``/products/BTC-USD/ticker``) — public, no auth."""
    if not path.startswith("/"):
        path = "/" + path
    url = f"{_EXCHANGE_REST_URL}{path}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "EzrasShark/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30, context=_get_ssl_context()) as resp:
        raw = resp.read().decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _brokerage_public_request(path: str) -> Any:
    """
    Unauthenticated GET under ``/api/v3/brokerage`` — public **market** routes only, e.g.
    ``/market/products``, ``/market/products/BTC-USD/ticker``.  (``/products`` without
    ``market`` returns 401.)
    """
    if not path.startswith("/"):
        path = "/" + path
    url = f"{_ADV_PUBLIC_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "EzrasShark/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=45, context=_get_ssl_context()) as resp:
        raw = resp.read().decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


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
    from the API key name (``COINBASE_API_KEY_NAME`` or ``COINBASE_API_KEY``) and
    EC PEM (``COINBASE_API_PRIVATE_KEY`` or ``COINBASE_API_SECRET``).
    No third-party JWT library required — uses ``cryptography``.
    """

    def __init__(
        self,
        api_key_name: Optional[str] = None,
        private_key_pem: Optional[str] = None,
        base_url: str = _ADV_BASE_URL,
    ) -> None:
        self._api_key_override = api_key_name
        self._pem_override = private_key_pem
        self.base_url = base_url.rstrip("/")
        self._key_name = ""
        self._pem = ""
        self._private_key: Optional[Any] = None
        self.last_error: Optional[str] = None
        self.marked_down: bool = False
        self._sync_credentials_from_env()

    def _sync_credentials_from_env(self) -> None:
        """Load key name + PEM from ctor args or env (both naming conventions)."""
        api_key = (
            (self._api_key_override if self._api_key_override is not None else None)
            or os.environ.get("COINBASE_API_KEY_NAME")
            or os.environ.get("COINBASE_API_KEY")
            or ""
        ).strip()
        raw_pem = (
            (self._pem_override if self._pem_override is not None else None)
            or os.environ.get("COINBASE_API_PRIVATE_KEY")
            or os.environ.get("COINBASE_API_SECRET")
            or ""
        )
        raw_pem = (raw_pem or "").strip()
        pem = normalize_coinbase_key_material(raw_pem)
        prev_k = getattr(self, "_key_name", "")
        prev_p = getattr(self, "_pem", "")
        if api_key == prev_k and pem == prev_p and self._private_key is not None:
            return
        self._key_name = api_key
        self._pem = pem
        self._private_key = None
        if not pem:
            return
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
        self._sync_credentials_from_env()
        key = (
            os.environ.get("COINBASE_API_KEY_NAME")
            or os.environ.get("COINBASE_API_KEY")
            or ""
        ).strip()
        secret = (
            os.environ.get("COINBASE_API_PRIVATE_KEY")
            or os.environ.get("COINBASE_API_SECRET")
            or ""
        ).strip()
        logger.info(
            "Coinbase credentials: key=%s secret=%s",
            bool(key),
            bool(secret),
        )
        return self._credentials_ready()

    def _credentials_ready(self) -> bool:
        return bool(self._key_name) and bool(self._pem) and self._private_key is not None

    def _order_guarded_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        _retry_5xx: int = 0,
    ) -> Dict[str, Any]:
        """POST /orders only after :func:`assert_live_order_permitted` in public entrypoints."""
        tok = _COINBASE_ORDER_GUARD.set(True)
        try:
            return self._request(
                method, path, params=params, body=body, _retry_5xx=_retry_5xx
            )
        finally:
            _COINBASE_ORDER_GUARD.reset(tok)

    # ── JWT ───────────────────────────────────────────────────────────────────

    def _build_jwt(self, method: str, request_path: str) -> str:
        """
        Build an ES256 JWT per the Coinbase Advanced Trade API v3 specification.

        Header:  ``{"alg":"ES256","kid":"<key_name>","nonce":"<32-hex>"}``
                  (``nonce`` is required by CDP; 16 random bytes as hex.)
        Payload: ``{"sub":"<key_name>","iss":"cdp","nbf":<now>,"exp":<now+120>,
                    "uri":"<METHOD> api.coinbase.com<full-path>"}``
        ``request_path`` must be the **path only** (no ``?query``), including
        ``/api/v3/brokerage/...``, matching ``coinbase-advanced-py`` REST JWTs. Query
        parameters belong on the HTTP URL only, not in the signed ``uri`` claim.

        Signature: ECDSA-P256-SHA256 over r‖s (32 B each), base64url (no padding).
        """
        uri_claim = f"{method.upper()} {_JWT_HOST}{request_path}"
        jwt_token = self._build_jwt_with_uri_claim(uri_claim)

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

    def _build_jwt_with_uri_claim(self, uri_claim: str) -> str:
        """Build ES256 JWT with an exact ``uri`` claim (REST path or WebSocket host line)."""
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
            "uri": uri_claim,
        }
        h64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        p64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h64}.{p64}".encode()
        raw_sig = self._private_key.sign(  # type: ignore[union-attr]
            signing_input, ec.ECDSA(hashes.SHA256())
        )
        r, s = decode_dss_signature(raw_sig)
        sig64 = _b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"{h64}.{p64}.{sig64}"

    def _build_jwt_minimal_cdp(self) -> str:
        """
        WebSocket JWT per CDP: ``sub``, ``iss``, ``nbf``, ``exp`` only (no ``uri``).

        Advanced Trade user WebSocket auth differs from REST JWT (which uses ``uri``).
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        now = int(time.time())
        payload: Dict[str, Any] = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
        }
        header: Dict[str, Any] = {
            "alg": "ES256",
            "kid": self._key_name,
            "nonce": secrets.token_hex(16),
        }
        h64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        p64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h64}.{p64}".encode()
        raw_sig = self._private_key.sign(  # type: ignore[union-attr]
            signing_input, ec.ECDSA(hashes.SHA256())
        )
        r, s = decode_dss_signature(raw_sig)
        sig64 = _b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"{h64}.{p64}.{sig64}"

    def build_user_stream_jwt(self) -> str:
        """
        JWT for ``wss://advanced-trade-ws-user.coinbase.com`` — user channel (orders/fills).

        Default: **CDP minimal** payload (no ``uri``), per Advanced Trade WebSocket docs.
        Override: ``NTE_COINBASE_USER_WS_JWT_MODE=legacy_uri`` for older ``GET host`` claim.
        """
        self._sync_credentials_from_env()
        if not self._credentials_ready():
            raise CoinbaseAuthError("Coinbase credentials not configured")
        mode = (os.environ.get("NTE_COINBASE_USER_WS_JWT_MODE") or "cdp_minimal").strip().lower()
        if mode in ("legacy_uri", "rest_uri", "uri"):
            return self._build_jwt_with_uri_claim("GET advanced-trade-ws-user.coinbase.com")
        return self._build_jwt_minimal_cdp()

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
        self._sync_credentials_from_env()
        if not self._credentials_ready():
            raise CoinbaseAuthError("Coinbase credentials not configured")

        # Query string: only on the real HTTP URL. CDP REST JWT ``uri`` claim must match
        # ``coinbase.rest`` / ``coinbase.jwt_generator.format_jwt_uri`` — host + path **without**
        # ``?query`` (see ``RESTBase.set_headers``: ``f"{method} {base_url}{url_path}"`` where
        # ``url_path`` excludes params). Including ``?limit=…`` in the signed uri breaks verification (401).
        qs = _query_string(params)
        rel_path = path if path.startswith("/") else f"/{path}"
        if (
            method.upper() == "POST"
            and "/orders" in rel_path
            and not _COINBASE_ORDER_GUARD.get()
        ):
            raise RuntimeError(
                "Coinbase order POST blocked: use place_market_buy, place_market_sell, "
                "place_limit_gtc, or cancel_order so live-order guard runs"
            )
        base = urlparse(self.base_url)
        broker_prefix = base.path.rstrip("/")
        jwt_path_only = f"{broker_prefix}{rel_path}"
        jwt_token = self._build_jwt(method, jwt_path_only)
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        portfolio_id = (os.environ.get("COINBASE_PORTFOLIO_ID") or "").strip()
        if portfolio_id:
            headers["CB-PORTFOLIO-ID"] = portfolio_id

        url = f"{base.scheme}://{base.netloc}{broker_prefix}{rel_path}{qs}"
        data: Optional[bytes] = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        ssl_ctx = _get_ssl_context()

        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {}
            out = json.loads(raw)
            out_dict = out if isinstance(out, dict) else {"_data": out}
            logger.info("API response: %s", str(out_dict)[:200])
            return out_dict
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

    def list_all_accounts(self) -> List[Dict[str, Any]]:
        """GET /accounts with cursor pagination until ``has_next`` is false — full balance picture."""
        aggregated: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(500):
            params: Dict[str, Any] = {"limit": 250}
            if cursor:
                params["cursor"] = cursor
            j = self._request("GET", "/accounts", params=params)
            batch = j.get("accounts") or []
            if isinstance(batch, list):
                aggregated.extend([a for a in batch if isinstance(a, dict)])
            has_next = bool(j.get("has_next"))
            cursor = (j.get("cursor") or j.get("next_cursor") or "").strip() or None
            if not has_next:
                break
            if not cursor:
                break
            if not batch:
                break
        return aggregated

    def get_accounts(self) -> List[Dict[str, Any]]:
        """GET /accounts — all pages (see :meth:`list_all_accounts`)."""
        return self.list_all_accounts()

    @staticmethod
    def _safe_float(x: Any) -> float:
        try:
            if x is None or x == "":
                return 0.0
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _portfolio_balances_dict_to_usd(pb: Dict[str, Any]) -> float:
        """
        Retail Advanced Trade shape: ``portfolio_balances`` is a dict of
        ``total_cash_equivalent_balance`` / ``total_balance`` / … each
        ``{value, currency}`` in USD.
        """
        if not isinstance(pb, dict):
            return 0.0

        def amount(key: str) -> float:
            node = pb.get(key)
            if not isinstance(node, dict):
                return 0.0
            cur = str(node.get("currency", "") or "").upper()
            if cur not in ("USD", "USDC", ""):
                return 0.0
            return CoinbaseClient._safe_float(node.get("value", 0) or 0)

        for key in ("total_cash_equivalent_balance", "total_balance"):
            v = amount(key)
            if v > 0:
                return v
        return 0.0

    @staticmethod
    def _account_usd_usdc_spendable(a: Dict[str, Any]) -> float:
        """
        Best-effort spendable fiat from one ``/accounts`` row.

        Coinbase may expose cash in ``available_balance`` and/or top-level ``balance``;
        we take the max of numeric ``value`` fields when both are dicts.
        """
        bal1 = a.get("available_balance")
        bal2 = a.get("balance")
        if not isinstance(bal1, dict):
            bal1 = {}
        if not isinstance(bal2, dict):
            bal2 = {}
        try:
            val1 = float(bal1.get("value", 0) or 0)
        except (TypeError, ValueError):
            val1 = 0.0
        try:
            val2 = float(bal2.get("value", 0) or 0)
        except (TypeError, ValueError):
            val2 = 0.0
        return max(val1, val2)

    @staticmethod
    def _sum_usd_usdc_from_accounts_json(j: Dict[str, Any]) -> float:
        """Sum USD + USDC from ``/accounts`` JSON (available vs balance, whichever is higher per row)."""
        total = 0.0
        for a in j.get("accounts", []) or []:
            if not isinstance(a, dict):
                continue
            curr = str(a.get("currency") or "").upper()
            if curr not in ("USD", "USDC"):
                continue
            val = CoinbaseClient._account_usd_usdc_spendable(a)
            total += val
            logger.info("Balance: %s $%.2f", curr, val)
        return total

    @staticmethod
    def _fiat_line_usd_usdc(item: Dict[str, Any]) -> float:
        """Extract a fiat USD/USDC amount from one portfolio breakdown / balance row."""
        asset = item.get("asset") if isinstance(item.get("asset"), dict) else {}
        code = (
            str(asset.get("asset_code") or item.get("currency") or item.get("asset_code") or "")
            .upper()
        )
        if code not in ("USD", "USDC"):
            return 0.0
        for key in (
            "total_balance_fiat",
            "available_balance_fiat",
            "fiat_value",
            "value",
            "balance",
        ):
            raw = item.get(key)
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
        for nest_key in ("available_balance", "total_balance", "fiat_balance"):
            nested = item.get(nest_key)
            if isinstance(nested, dict):
                try:
                    return float(nested.get("value") or nested.get("amount") or 0.0)
                except (TypeError, ValueError):
                    pass
        return 0.0

    def _parse_portfolio_balances_json(self, data: Dict[str, Any]) -> float:
        """Sum USD + USDC fiat from portfolio ``/balances`` or breakdown payloads (flexible keys)."""
        total = 0.0
        bd = data.get("breakdown")
        nested_pb: Optional[Dict[str, Any]] = None
        if isinstance(bd, dict):
            maybe = bd.get("portfolio_balances")
            if isinstance(maybe, dict):
                nested_pb = maybe
                total += self._portfolio_balances_dict_to_usd(maybe)
        top_pb = data.get("portfolio_balances")
        if isinstance(top_pb, dict) and top_pb is not nested_pb:
            total += self._portfolio_balances_dict_to_usd(top_pb)

        for key in ("breakdown", "portfolio_breakdown", "balances", "spot_balances"):
            block = data.get(key)
            if isinstance(block, list):
                for item in block:
                    if isinstance(item, dict):
                        add = self._fiat_line_usd_usdc(item)
                        total += add
                        if add:
                            acode = str(
                                (item.get("asset") or {}).get("asset_code")
                                or item.get("currency")
                                or "?"
                            ).upper()
                            logger.info("Portfolio balance: %s $%.2f", acode, add)
        # intx-style nested blocks
        for pb in data.get("portfolio_balances") or []:
            if not isinstance(pb, dict):
                continue
            for bal in pb.get("balances") or []:
                if isinstance(bal, dict):
                    total += self._fiat_line_usd_usdc(bal)
        return total

    def get_portfolio_balance(self) -> float:
        """
        When ``COINBASE_PORTFOLIO_ID`` is set, prefer portfolio balance endpoints, then ``/accounts``.
        """
        portfolio_id = (os.environ.get("COINBASE_PORTFOLIO_ID") or "").strip()
        if not portfolio_id:
            return 0.0
        for path in (
            f"/portfolios/{portfolio_id}/balances",
            f"/portfolios/{portfolio_id}",
        ):
            try:
                j = self._request("GET", path)
                t = self._parse_portfolio_balances_json(j)
                if t > 0:
                    return t
            except Exception as e:
                logger.warning("get_portfolio_balance %s: %s", path, e)
        try:
            return self._sum_usd_usdc_from_accounts_json({"accounts": self.list_all_accounts()})
        except Exception as e:
            logger.warning("get_portfolio_balance accounts fallback: %s", e)
            return 0.0

    def _parse_fiat_from_portfolio(
        self, j: Dict[str, Any], portfolio_id: str
    ) -> float:
        """Parse USD/USDC from portfolio ``/balances``, ``/portfolios/{id}``, or list payloads."""
        total = 0.0
        breakdown = j.get("breakdown")
        if isinstance(breakdown, dict):
            inner_pb = breakdown.get("portfolio_balances")
            if isinstance(inner_pb, dict):
                total += self._portfolio_balances_dict_to_usd(inner_pb)
        if isinstance(breakdown, list):
            for item in breakdown:
                if not isinstance(item, dict):
                    continue
                asset = item.get("asset", {})
                if not isinstance(asset, dict):
                    asset = {}
                code = str(asset.get("asset_code", "") or "").upper()
                if code in ("USD", "USDC"):
                    total += self._safe_float(item.get("total_balance_fiat", 0) or 0)

        portfolios = j.get("portfolios")
        if isinstance(portfolios, list):
            for p in portfolios:
                if not isinstance(p, dict):
                    continue
                if p.get("uuid") == portfolio_id:
                    bal = p.get("balance", {})
                    if isinstance(bal, dict):
                        total += self._safe_float(bal.get("value", 0) or 0)
                    else:
                        total += self._safe_float(bal or 0)

        if not total:
            bal = j.get("balance", {})
            if isinstance(bal, dict):
                total += self._safe_float(bal.get("value", 0) or 0)

        spot = j.get("spot_positions")
        if isinstance(spot, list):
            for pos in spot:
                if not isinstance(pos, dict):
                    continue
                asset = str(pos.get("asset", "") or "").upper()
                if asset in ("USD", "USDC"):
                    total += self._safe_float(pos.get("total_balance_fiat", 0) or 0)

        if total <= 0:
            total = self._parse_portfolio_balances_json(j)
        return total

    def _get_portfolio_fiat(self, portfolio_id: str) -> float:
        """USD/USDC from portfolio endpoints (consumer cash is often only here)."""
        endpoints = [
            f"/portfolios/{portfolio_id}/balances",
            f"/portfolios/{portfolio_id}",
            "/portfolios",
        ]
        for endpoint in endpoints:
            try:
                j = self._request("GET", endpoint)
                logger.info("Portfolio endpoint %s: %s", endpoint, str(j)[:500])
                total = self._parse_fiat_from_portfolio(j, portfolio_id)
                if total > 0:
                    logger.info("Found $%.2f at %s", total, endpoint)
                    return total
            except Exception as e:
                logger.warning("Portfolio endpoint %s: %s", endpoint, e)
        return 0.0

    def get_usd_balance(self) -> float:
        """
        Spendable USD + USDC: optional ``COINBASE_BALANCE_OVERRIDE``, then ``/accounts``,
        then portfolio fiat (``COINBASE_PORTFOLIO_ID``, ``retail_portfolio_id`` from accounts,
        or ``KNOWN_PORTFOLIO_ID``).
        """
        override = (os.environ.get("COINBASE_BALANCE_OVERRIDE") or "").strip()
        if override:
            try:
                return float(override)
            except ValueError:
                pass

        try:
            accounts = self.list_all_accounts()
            total = 0.0
            detected_portfolio_id: Optional[str] = None
            for a in accounts:
                if not isinstance(a, dict):
                    continue
                curr = str(a.get("currency", "") or "").upper()
                if not detected_portfolio_id:
                    rp = a.get("retail_portfolio_id")
                    if rp:
                        detected_portfolio_id = str(rp).strip()
                val = self._account_usd_usdc_spendable(a)
                if curr in ("USD", "USDC"):
                    total += val
                    logger.info("USD row: %s $%.2f", curr, val)

            if total > 0:
                return total

            pid = (
                (os.environ.get("COINBASE_PORTFOLIO_ID") or "").strip()
                or (detected_portfolio_id or "")
                or KNOWN_PORTFOLIO_ID
                or ""
            ).strip()

            if pid:
                logger.info("No USD in accounts, trying portfolio %s", pid)
                return self._get_portfolio_fiat(pid)

        except Exception as e:
            logger.warning("get_usd_balance: %s", e)
        return 0.0

    def debug_all_balances(self) -> Dict[str, Any]:
        """Fetch ``/accounts`` and ``/portfolios`` for diagnostics."""
        results: Dict[str, Any] = {}
        try:
            j = self._request("GET", "/accounts")
            results["accounts"] = j
        except Exception as e:
            results["accounts_error"] = str(e)

        try:
            j2 = self._request("GET", "/portfolios")
            results["portfolios"] = j2
        except Exception as e:
            results["portfolios_error"] = str(e)

        return results

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

    def get_product_price(self, product_id: str) -> Tuple[float, float]:
        """
        Return (bid, ask) using the same **public** path as :meth:`get_prices`
        (``/api/v3/brokerage/market/products/{id}/ticker``, then Exchange fallback).
        """
        return self._get_public_ticker_bid_ask(product_id)

    def _get_public_ticker_bid_ask(self, product_id: str) -> Tuple[float, float]:
        """
        Primary: **public** ``GET /api/v3/brokerage/market/products/{id}/ticker`` (``best_bid`` /
        ``best_ask``) — no JWT.

        Fallback: Coinbase Exchange ``GET /products/{id}/ticker``, then v2 spot (mid only).
        """
        safe = urllib.parse.quote(str(product_id).strip(), safe="-")

        def _f(x: Any) -> float:
            try:
                if x is None or x == "":
                    return 0.0
                return float(x)
            except (TypeError, ValueError):
                return 0.0

        try:
            j = _brokerage_public_request(f"/market/products/{safe}/ticker")
            bid = _f(j.get("best_bid"))
            ask = _f(j.get("best_ask"))
            if bid > 0 and ask > 0:
                return bid, ask
            if bid > 0 or ask > 0:
                mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
                if bid <= 0:
                    bid = mid
                if ask <= 0:
                    ask = mid
                return bid, ask
        except Exception as exc:
            logger.debug("Coinbase market ticker %s: %s", product_id, exc)

        try:
            j = _exchange_public_request(f"/products/{safe}/ticker")
        except Exception as exc:
            logger.debug("Coinbase Exchange ticker %s: %s", product_id, exc)
            p = _public_spot(product_id)
            if p is None or p <= 0:
                return 0.0, 0.0
            return p, p

        bid = _f(j.get("bid"))
        ask = _f(j.get("ask"))
        if bid <= 0 and ask <= 0:
            p = _f(j.get("price"))
            if p > 0:
                return p, p
            p2 = _public_spot(product_id)
            if p2 is not None and p2 > 0:
                return p2, p2
            return 0.0, 0.0
        if bid <= 0 or ask <= 0:
            mid = _f(j.get("price"))
            if mid > 0:
                if bid <= 0:
                    bid = mid
                if ask <= 0:
                    ask = mid
        return bid, ask

    def get_prices(self, product_ids: List[str]) -> Dict[str, Tuple[float, float]]:
        """
        Fetch bid/ask per product via **public** ``/market/products/{id}/ticker`` (no JWT).

        Does not use authenticated CDP ``/best_bid_ask``.
        """
        if not product_ids:
            return {}
        result: Dict[str, Tuple[float, float]] = {}
        for pid in dict.fromkeys(product_ids):
            bid, ask = self._get_public_ticker_bid_ask(pid)
            if bid > 0 or ask > 0:
                result[pid] = (bid, ask)
        return result

    def get_prices_batched(
        self,
        product_ids: List[str],
        *,
        chunk_size: int = 80,
    ) -> Dict[str, Tuple[float, float]]:
        """Same as :meth:`get_prices` but splits into chunks to avoid oversized query strings."""
        out: Dict[str, Tuple[float, float]] = {}
        ids = list(dict.fromkeys(product_ids))
        for i in range(0, len(ids), max(1, chunk_size)):
            chunk = ids[i : i + chunk_size]
            out.update(self.get_prices(chunk))
        return out

    def list_exchange_usd_products(self) -> List[Dict[str, Any]]:
        """
        **Online** USD-quoted products from Coinbase Exchange
        ``GET https://api.exchange.coinbase.com/products`` (public JSON array, no JWT).

        Advanced Trade spot universe is **crypto** pairs here (e.g. ``BTC-USD``). Rows
        include ``status``, ``quote_currency``, optional ``product_type`` when present.
        """
        try:
            data = _exchange_public_request("/products")
        except Exception as exc:
            logger.warning("Coinbase Exchange GET /products failed: %s", exc)
            return []
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            if str(row.get("quote_currency") or "") != "USD":
                continue
            if str(row.get("status") or "") != "online":
                continue
            if row.get("trading_disabled"):
                continue
            pid = str(row.get("id") or "").strip()
            if not pid.endswith("-USD"):
                continue
            out.append(
                {
                    "product_id": pid,
                    "quote_currency": "USD",
                    "base_currency": str(row.get("base_currency") or ""),
                    "product_type": str(
                        row.get("product_type") or row.get("type") or "SPOT"
                    ),
                }
            )
        return out

    def get_exchange_product_stats(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Public ``GET /products/{product_id}/stats`` — open, high, low, last, volume (24h), etc.
        No authentication.
        """
        safe = urllib.parse.quote(str(product_id).strip(), safe="")
        try:
            data = _exchange_public_request(f"/products/{safe}/stats")
        except Exception as exc:
            logger.debug(
                "Coinbase Exchange GET /products/%s/stats failed: %s", product_id, exc
            )
            return None
        if not isinstance(data, dict):
            return None
        return data

    def list_brokerage_products(self) -> List[Dict[str, Any]]:
        """
        Tradable SPOT USD products for scanners — **public**
        ``GET /api/v3/brokerage/market/products`` (paginated, no JWT).

        Rows include ``approximate_quote_24h_volume`` from the API (string or float).
        """
        out: List[Dict[str, Any]] = []
        limit = 500
        offset = 0
        try:
            while True:
                path = f"/market/products?limit={limit}&offset={offset}&product_type=SPOT"
                j = _brokerage_public_request(path)
                rows = j.get("products") or []
                if not isinstance(rows, list) or not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("quote_currency_id") or "").upper() != "USD":
                        continue
                    if str(row.get("product_type") or "").upper() != "SPOT":
                        continue
                    if row.get("status") != "online":
                        continue
                    if row.get("trading_disabled") or row.get("is_disabled"):
                        continue
                    pid = str(row.get("product_id") or "").strip()
                    if not pid.endswith("-USD"):
                        continue
                    vol = row.get("approximate_quote_24h_volume")
                    try:
                        v24 = float(vol) if vol is not None and str(vol).strip() != "" else 0.0
                    except (TypeError, ValueError):
                        v24 = 0.0
                    out.append(
                        {
                            "product_id": pid,
                            "quote_currency_id": "USD",
                            "approximate_quote_24h_volume": v24,
                            "base_currency_id": row.get("base_currency_id") or "",
                        }
                    )
                if len(rows) < limit:
                    break
                offset += limit
        except Exception as exc:
            logger.warning("Coinbase market /products failed: %s", exc)
            return []

        return out

    # ── orders ────────────────────────────────────────────────────────────────

    def place_market_buy(self, product_id: str, usd_amount: float) -> OrderResult:
        """
        Market buy ``usd_amount`` USD worth of ``product_id``.
        Uses ``market_market_ioc`` with ``quote_size`` (USD spend).
        """
        self._sync_credentials_from_env()
        try:
            assert_live_order_permitted(
                "place_market_entry",
                "coinbase",
                product_id,
                strategy_id=None,
                source="coinbase_client",
                order_side="BUY",
                quote_notional=float(usd_amount),
            )
        except RuntimeError as exc:
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="error",
                outlet="coinbase",
                success=False,
                reason=str(exc),
                raw={},
            )
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
            j = self._order_guarded_request("POST", "/orders", body=body)
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
        self._sync_credentials_from_env()
        try:
            assert_live_order_permitted(
                "place_market_exit",
                "coinbase",
                product_id,
                strategy_id=None,
                source="coinbase_client",
                order_side="SELL",
                base_size=str(base_size),
            )
        except RuntimeError as exc:
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="error",
                outlet="coinbase",
                success=False,
                reason=str(exc),
                raw={},
            )
        client_order_id = uuid.uuid4().hex
        body = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": "SELL",
            "order_configuration": {
                "market_market_ioc": {"base_size": str(base_size)}
            },
        }
        logger.warning("PLACE SELL: %s size=%s", product_id, base_size)
        logger.warning("SELL ORDER BODY: %s", json.dumps(body))
        try:
            j = self._order_guarded_request("POST", "/orders", body=body)
            try:
                resp_s = json.dumps(j) if isinstance(j, dict) else str(j)
            except (TypeError, ValueError):
                resp_s = str(j)
            logger.warning("SELL RESPONSE: %s", resp_s[:500])
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
        self._sync_credentials_from_env()
        try:
            assert_live_order_permitted(
                "cancel_order",
                "coinbase",
                "*",
                strategy_id=None,
                source="coinbase_client",
            )
        except RuntimeError:
            return False
        try:
            j = self._order_guarded_request(
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

    def place_limit_gtc(
        self,
        product_id: str,
        side: str,
        base_size: str,
        limit_price: str,
        *,
        post_only: bool = True,
    ) -> OrderResult:
        """
        GTC limit order (maker when ``post_only`` and price does not cross the book).

        ``order_configuration.limit_limit_gtc`` per Advanced Trade API.
        """
        client_order_id = uuid.uuid4().hex
        cfg: Dict[str, Any] = {
            "base_size": str(base_size),
            "limit_price": str(limit_price),
        }
        if post_only:
            cfg["post_only"] = True
        body = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": side.strip().upper(),
            "order_configuration": {"limit_limit_gtc": cfg},
        }
        self._sync_credentials_from_env()
        side_u = side.strip().upper()
        try:
            lim_action = (
                "place_limit_entry" if side_u == "BUY" else "place_market_exit"
            )
            assert_live_order_permitted(
                lim_action,
                "coinbase",
                product_id,
                strategy_id=None,
                source="coinbase_client",
                order_side=side_u,
                base_size=str(base_size),
            )
        except RuntimeError as exc:
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
        try:
            j = self._order_guarded_request("POST", "/orders", body=body)
            success = bool(j.get("success"))
            sr = j.get("success_response") or {}
            order_id = str(sr.get("order_id") or j.get("order_id") or client_order_id)
            if not success:
                err = j.get("error_response") or {}
                reason = str(err.get("message") or err.get("error") or "limit order failed")
                logger.warning(
                    "Coinbase LIMIT %s failed (%s): %s",
                    side.upper(),
                    product_id,
                    reason,
                )
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
                "Coinbase LIMIT %s placed: %s size=%s @ %s → order_id=%s",
                side.upper(),
                product_id,
                base_size,
                limit_price,
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
            logger.error("Coinbase LIMIT exception (%s): %s", product_id, exc)
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
        try:
            act = (
                "place_market_entry"
                if str(side).strip().lower() == "buy"
                else "place_market_exit"
            )
            assert_live_order_permitted(
                act,
                "coinbase",
                product_id,
                strategy_id=None,
                source="coinbase_fetcher",
                order_side=str(side).strip().upper(),
                base_size=str(size),
            )
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
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
        try:
            su = str(side).strip().upper()
            lim_action = "place_limit_entry" if su == "BUY" else "place_market_exit"
            assert_live_order_permitted(
                lim_action,
                "coinbase",
                product_id,
                strategy_id=None,
                source="coinbase_fetcher",
                order_side=su,
                base_size=str(size),
            )
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
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
