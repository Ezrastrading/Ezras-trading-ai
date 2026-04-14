"""
Kalshi Trade API v2 — https://api.elections.kalshi.com/trade-api/v2

Authentication (per Kalshi docs): RSA private key + API Key ID, not a Bearer token.
Each request is signed with RSA-PSS + SHA256 over ``timestamp_ms + HTTP_METHOD + path``
(path only, no query string), with headers:

- ``KALSHI-ACCESS-KEY``: Key ID (UUID from Kalshi account)
- ``KALSHI-ACCESS-TIMESTAMP``: milliseconds
- ``KALSHI-ACCESS-SIGNATURE``: base64(RSA-PSS-SHA256 signature)

Legacy: a short opaque ``KALSHI_API_KEY`` is still sent as ``Authorization: Bearer …``.

``KALSHI_API_KEY`` may hold a PEM private key with literal ``\\n`` in ``.env``; those are
normalized to real newlines before loading.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot, OrderResult
from trading_ai.shark.outlets.base import BaseOutletFetcher

logger = logging.getLogger(__name__)

load_shark_dotenv()

# Production Kalshi hosts often include /trade-api/v2; env override supported.
_DEFAULT_BASE = os.environ.get("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2").rstrip("/")


class KalshiAuthError(Exception):
    pass


def is_kalshi_pem_private_key(material: str) -> bool:
    m = material.strip()
    return "-----BEGIN" in m and "PRIVATE KEY-----" in m


def normalize_kalshi_key_material(raw: str) -> str:
    """Turn .env single-line PEM (with ``\\n`` escapes) into a proper PEM string."""
    if not raw:
        return raw
    normalized = raw.replace("\\n", "\n")
    normalized = normalized.strip()
    if is_kalshi_pem_private_key(normalized):
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            load_pem_private_key(normalized.encode("utf-8"), password=None)
            logger.info("Kalshi RSA key loaded OK")
        except Exception as e:
            logger.error("Kalshi PEM failed: %s", e)
            logger.error("Key preview: %s", normalized[:80])
    return normalized


def _load_rsa_private_key(pem: str) -> Any:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    data = pem.encode("utf-8") if isinstance(pem, str) else pem
    return serialization.load_pem_private_key(data, password=None, backend=default_backend())


def sign_kalshi_pss_sha256(private_key: Any, timestamp_ms: str, method: str, path_without_query: str) -> str:
    """RSA-PSS + SHA256 over ``timestamp_ms + method + path``; base64 signature (Kalshi API)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    msg = f"{timestamp_ms}{method.upper()}{path_without_query}".encode("utf-8")
    sig = private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")


def path_for_kalshi_signature(full_url: str) -> str:
    """Path used in the signature string (no query), e.g. ``/trade-api/v2/markets``."""
    return urllib.parse.urlparse(full_url).path.split("?")[0]


class KalshiClient:
    """HTTP client with retries, RSA-PSS or Bearer auth, health + orders."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = _DEFAULT_BASE) -> None:
        raw = api_key if api_key is not None else os.environ.get("KALSHI_API_KEY")
        self.api_key = normalize_kalshi_key_material(raw or "")
        self.base_url = base_url.rstrip("/")
        self.last_error: Optional[str] = None
        self.marked_down: bool = False

        self._rsa_private_key: Optional[Any] = None
        self.access_key_id: str = (os.environ.get("KALSHI_ACCESS_KEY_ID") or "").strip()
        self._use_rsa: bool = False

        if self.api_key and is_kalshi_pem_private_key(self.api_key):
            try:
                self._rsa_private_key = _load_rsa_private_key(self.api_key)
                self._use_rsa = True
            except Exception as exc:
                logger.warning("Kalshi: could not load PEM from KALSHI_API_KEY: %s", exc)
                self._rsa_private_key = None
                self._use_rsa = False

    def uses_rsa_auth(self) -> bool:
        return bool(self._use_rsa and self._rsa_private_key and self.access_key_id)

    def has_kalshi_credentials(self) -> bool:
        if self.uses_rsa_auth():
            return True
        return bool(self.api_key) and not is_kalshi_pem_private_key(self.api_key)

    def _auth_headers(self, method: str, full_url: str) -> Dict[str, str]:
        h: Dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.uses_rsa_auth():
            assert self._rsa_private_key is not None
            ts = str(int(time.time() * 1000))
            sign_path = path_for_kalshi_signature(full_url)
            sig = sign_kalshi_pss_sha256(self._rsa_private_key, ts, method, sign_path)
            h["KALSHI-ACCESS-KEY"] = self.access_key_id
            h["KALSHI-ACCESS-TIMESTAMP"] = ts
            h["KALSHI-ACCESS-SIGNATURE"] = sig
            return h
        if self.api_key and not is_kalshi_pem_private_key(self.api_key):
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

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
            raise RuntimeError("kalshi outlet marked down")
        qs = ""
        if params:
            qs = "?" + urllib.parse.urlencode({k: str(v) for k, v in params.items() if v is not None})
        url = f"{self.base_url}{path}{qs}"
        data: Optional[bytes] = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=self._auth_headers(method, url))
        try:
            raw = ""
            insecure_ctx = ssl.create_default_context()
            insecure_ctx.check_hostname = False
            insecure_ctx.verify_mode = ssl.CERT_NONE
            last_ssl: Optional[BaseException] = None
            for attempt in range(3):
                got = False
                for use_relaxed in (False, True):
                    try:
                        opener_kw: Dict[str, Any] = {"timeout": 45}
                        if use_relaxed:
                            opener_kw["context"] = insecure_ctx
                        with urllib.request.urlopen(req, **opener_kw) as resp:
                            raw = resp.read().decode("utf-8")
                        got = True
                        break
                    except ssl.SSLError as e:
                        last_ssl = e
                        logger.warning(
                            "Kalshi SSL error (attempt %s/3, relaxed=%s): %s",
                            attempt + 1,
                            use_relaxed,
                            e,
                        )
                if got:
                    break
                if attempt < 2:
                    time.sleep(1)
                    continue
                logger.error("Kalshi SSL failed 3x")
                raise last_ssl if last_ssl else ssl.SSLError("Kalshi SSL failed")
            if not raw.strip():
                return {}
            out = json.loads(raw)
            return out if isinstance(out, dict) else {"_data": out}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.warning(
                    "Kalshi: auth failed — scan-only mode or check API key permissions (401)"
                )
                self.last_error = "401"
                raise KalshiAuthError("401 unauthorized") from e
            if e.code == 429:
                logger.warning("Kalshi 429 — backing off 30s")
                time.sleep(30)
                return self._request(method, path, params=params, body=body, _retry_5xx=_retry_5xx)
            if 500 <= e.code < 600 and _retry_5xx < 3:
                delay = (2**_retry_5xx) * 0.5
                logger.warning("Kalshi %s — retry %s after %.1fs", e.code, _retry_5xx + 1, delay)
                time.sleep(delay)
                return self._request(method, path, params=params, body=body, _retry_5xx=_retry_5xx + 1)
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = str(e)
            raise RuntimeError(f"Kalshi HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            logger.error("Kalshi network error: %s", e)
            self.marked_down = True
            self.last_error = str(e)
            raise

    def health_exchange_status(self) -> Tuple[bool, str]:
        try:
            # Spec: GET /exchange/status — try under base
            for path in ("/exchange/status", "/status"):
                try:
                    j = self._request("GET", path)
                    st = str(j.get("status") or j.get("exchange", {}).get("status") or "")
                    if st.lower() == "active" or j.get("ok"):
                        return True, "active"
                    return bool(j), st or "unknown"
                except Exception:
                    continue
            return False, "no_status_endpoint"
        except Exception as exc:
            return False, str(exc)

    def fetch_markets_open(self, limit: int = 1000) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"limit": min(limit, 1000)}
            if cursor:
                params["cursor"] = cursor
            j = self._request("GET", "/markets", params=params)
            batch = j.get("markets") or j.get("data") or []
            if isinstance(batch, list):
                rows.extend(batch)
            cur = j.get("cursor") or j.get("next_cursor")
            if not cur or (isinstance(batch, list) and len(batch) == 0):
                break
            cursor = str(cur)
            if len(rows) >= limit:
                break
        return rows[:limit]

    def fetch_orderbook_depth(self, ticker: str) -> Tuple[float, float]:
        j = self._request("GET", f"/markets/{urllib.parse.quote(ticker, safe='')}/orderbook")
        ob = j.get("orderbook") or j
        yes_d = 0.0
        no_d = 0.0
        for side_key in ("yes", "no"):
            levels = ob.get(side_key) or ob.get(f"{side_key}_orders") or []
            if not isinstance(levels, list):
                continue
            for lv in levels:
                sz = 0.0
                if isinstance(lv, (list, tuple)) and len(lv) >= 2:
                    sz = float(lv[1])
                elif isinstance(lv, dict):
                    sz = float(lv.get("size") or lv.get("count") or 0)
                if side_key == "yes":
                    yes_d += sz
                else:
                    no_d += sz
        return yes_d, no_d

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        count: int,
        yes_price_cents: int,
    ) -> OrderResult:
        if not self.has_kalshi_credentials():
            from trading_ai.shark.required_env import require_kalshi_api_key

            require_kalshi_api_key()
        body = {
            "ticker": ticker,
            "side": side,
            "type": "limit",
            "count": count,
            "yes_price": yes_price_cents,
        }
        j = self._request("POST", "/portfolio/orders", body=body)
        oid = str(j.get("order", {}).get("order_id") or j.get("order_id") or j.get("id") or "")
        status = str(j.get("status") or j.get("order", {}).get("status") or "submitted")
        fp = float(j.get("filled_price", 0) or j.get("order", {}).get("avg_price", 0) or 0) / 100.0
        fs = float(j.get("filled_count", 0) or j.get("order", {}).get("filled_count", 0) or 0)
        return OrderResult(
            order_id=oid or "unknown",
            filled_price=fp,
            filled_size=fs,
            timestamp=time.time(),
            status=status,
            outlet="kalshi",
            raw=j,
        )

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/portfolio/orders/{urllib.parse.quote(order_id, safe='')}")

    def get_market(self, ticker: str) -> Dict[str, Any]:
        return self._request("GET", f"/markets/{urllib.parse.quote(ticker, safe='')}")

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/portfolio/orders/{urllib.parse.quote(order_id, safe='')}")


def build_kalshi_request_headers(method: str, full_url: str) -> Dict[str, str]:
    """Build Kalshi auth headers from ``KALSHI_API_KEY`` / ``KALSHI_ACCESS_KEY_ID`` (for setup tools)."""
    c = KalshiClient()
    h = c._auth_headers(method, full_url)
    return h


def _parse_close_timestamp_unix(m: Dict[str, Any]) -> Optional[float]:
    """Absolute resolution time in unix seconds, if parseable from Kalshi market JSON."""
    ct = m.get("close_time") or m.get("expiration_time") or m.get("expected_expiration_time")
    if not ct:
        return None
    if isinstance(ct, (int, float)):
        ts = float(ct)
        if ts > 1e12:
            ts /= 1000.0
        return ts
    if isinstance(ct, str):
        try:
            dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            return float(dt.timestamp())
        except ValueError:
            pass
    return None


def _parse_close_time_seconds(m: Dict[str, Any], now: float) -> float:
    abs_ts = _parse_close_timestamp_unix(m)
    if abs_ts is not None:
        return max(60.0, abs_ts - now)
    return 86400.0


_KALSHI_TERMINAL_STATUSES = frozenset(
    {"closed", "settled", "finalized", "determined", "expired", "cancelled", "canceled"}
)


def _kalshi_market_tradeable(m: Dict[str, Any], now: float) -> bool:
    """Unsettled markets with close time in the future; status must not be a known terminal value."""
    if m.get("settled") or m.get("is_settled"):
        return False
    end = _parse_close_timestamp_unix(m)
    if end is not None and end <= now:
        return False
    st = str(m.get("status", "")).strip().lower()
    if st in _KALSHI_TERMINAL_STATUSES:
        return False
    return True


def map_kalshi_market_to_snapshot(m: Dict[str, Any], now: float) -> MarketSnapshot:
    ticker = str(m.get("ticker") or m.get("market_id") or "")
    yes_ask = float(m.get("yes_ask") or m.get("yes_bid") or 50)
    no_ask = float(m.get("no_ask") or m.get("no_bid") or 50)
    vol = float(m.get("volume_24h") or m.get("volume") or 0)
    title = str(m.get("title") or m.get("subtitle") or "")
    end_ts = _parse_close_timestamp_unix(m)
    return MarketSnapshot(
        market_id=ticker,
        outlet="kalshi",
        yes_price=yes_ask / 100.0,
        no_price=no_ask / 100.0,
        volume_24h=vol,
        time_to_resolution_seconds=_parse_close_time_seconds(m, now),
        resolution_criteria=title,
        last_price_update_timestamp=now,
        underlying_data_if_available={"kalshi_raw": m},
        market_category="kalshi",
        question_text=title or None,
        end_timestamp_unix=end_ts,
        end_date_seconds=end_ts,
    )


class KalshiFetcher(BaseOutletFetcher):
    outlet_name = "kalshi"

    def __init__(self) -> None:
        self._client = KalshiClient()

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        if not self._client.has_kalshi_credentials():
            if self._client.api_key and is_kalshi_pem_private_key(self._client.api_key) and not self._client.access_key_id:
                logger.warning(
                    "Kalshi: PEM in KALSHI_API_KEY but KALSHI_ACCESS_KEY_ID is missing — cannot authenticate"
                )
            else:
                logger.warning("KALSHI_API_KEY not set — Kalshi fetcher returns empty")
            return []
        try:
            now = time.time()
            raw_list = self._client.fetch_markets_open(limit=500)
            sample_statuses = [str(m.get("status", "unknown")) for m in raw_list[:20] if isinstance(m, dict)]
            logger.info("Kalshi sample statuses (first %s): %s", len(sample_statuses), sample_statuses)
            active_rows = [m for m in raw_list if isinstance(m, dict) and _kalshi_market_tradeable(m, now)]
            logger.info(
                "Kalshi: %s fetched → %s tradeable markets (settled/time/status filter)",
                len(raw_list),
                len(active_rows),
            )
            return [map_kalshi_market_to_snapshot(m, now) for m in active_rows]
        except KalshiAuthError:
            return []
        except Exception as exc:
            logger.warning("Kalshi fetch failed: %s", exc)
            self._client.marked_down = True
            return []
