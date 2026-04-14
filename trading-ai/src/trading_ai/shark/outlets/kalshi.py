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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot, OrderResult
from trading_ai.shark.outlets.base import BaseOutletFetcher

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

load_shark_dotenv()

try:
    from zoneinfo import ZoneInfo

    _KALSHI_ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _KALSHI_ET = None  # type: ignore[misc]

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


def _get_ssl_context() -> ssl.SSLContext:
    """CA bundle from certifi (Railway-friendly); fallback to system defaults."""
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception as exc:
            # ENOMEM during SSL setup — avoid formatting huge nested tracebacks
            if isinstance(exc, OSError) and getattr(exc, "errno", None) == 12:
                logger.debug("Kalshi: certifi context skipped (ENOMEM), using default SSL context")
            elif isinstance(exc, MemoryError):
                logger.debug("Kalshi: certifi context skipped (MemoryError), using default SSL context")
            else:
                logger.warning("Kalshi: certifi context failed (%s), using default", type(exc).__name__)
    return ssl.create_default_context()


def _kalshi_row_has_explicit_quotes(m: Dict[str, Any]) -> bool:
    """True if list-row payload already has at least one non-null quote field."""
    for k in ("yes_bid", "yes_ask", "yes_price", "last_price", "no_bid", "no_ask", "no_price"):
        if m.get(k) is not None:
            return True
    return False


def _parse_orderbook_yes_no_probs(ob_root: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Best bid from orderbook ``yes`` / ``no`` sides; prices as probabilities in (0, 1)."""
    ob = ob_root.get("orderbook") if isinstance(ob_root.get("orderbook"), dict) else ob_root
    if not isinstance(ob, dict):
        return None, None

    def _best_bid_cents(levels: Any) -> Optional[float]:
        if not isinstance(levels, list) or not levels:
            return None
        best: Optional[float] = None
        for lv in levels:
            if isinstance(lv, (list, tuple)) and len(lv) >= 1:
                c = float(lv[0])
            elif isinstance(lv, dict):
                c = float(lv.get("price", lv.get("price_cents", 0)) or 0)
            else:
                continue
            if c <= 0:
                continue
            if best is None or c > best:
                best = c
        return best

    yes_c = _best_bid_cents(ob.get("yes"))
    no_c = _best_bid_cents(ob.get("no"))
    yes_p = yes_c / 100.0 if yes_c is not None else None
    no_p = no_c / 100.0 if no_c is not None else None
    if yes_p is not None and yes_p > 1.0:
        yes_p = yes_p / 100.0
    if no_p is not None and no_p > 1.0:
        no_p = no_p / 100.0
    if yes_p is not None and no_p is None:
        no_p = max(0.01, min(0.99, 1.0 - yes_p))
    elif no_p is not None and yes_p is None:
        yes_p = max(0.01, min(0.99, 1.0 - no_p))
    return yes_p, no_p


def fetch_kalshi_market_price(
    ticker: str,
    client: Optional["KalshiClient"] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """YES/NO probabilities from ``GET /markets/{{ticker}}/orderbook`` best bids (cents → 0–1)."""
    c = client or KalshiClient()
    try:
        data = c._request("GET", f"/markets/{urllib.parse.quote(ticker.strip(), safe='')}/orderbook")
        return _parse_orderbook_yes_no_probs(data)
    except Exception:
        return None, None


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
            ssl_ctx = _get_ssl_context()
            last_ssl: Optional[BaseException] = None
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=45, context=ssl_ctx) as resp:
                        raw = resp.read().decode("utf-8")
                    break
                except ssl.SSLError as e:
                    last_ssl = e
                    logger.warning("Kalshi SSL error (attempt %s/3): %s", attempt + 1, e)
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    logger.error("Kalshi SSL failed after 3 attempts (certifi CA bundle)")
                    raise
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

    def fetch_markets_for_series(self, series_ticker: str, *, limit: int = 80) -> List[Dict[str, Any]]:
        """GET /markets with ``series_ticker`` (single-event series). Returns [] on error."""
        lim = max(1, min(int(limit), 200))
        bases = (
            {"series_ticker": series_ticker, "status": "open", "limit": lim, "include_prices": "true"},
            {"series_ticker": series_ticker, "status": "open", "limit": lim, "with_nested_markets": "true"},
            {"series_ticker": series_ticker, "status": "open", "limit": lim},
        )
        for params in bases:
            try:
                j = self._request("GET", "/markets", params=params)
            except Exception:
                continue
            batch = j.get("markets") or j.get("data") or []
            if isinstance(batch, list):
                return batch
        return []

    def enrich_market_with_detail_and_orderbook(self, m: Dict[str, Any]) -> Dict[str, Any]:
        """Merge GET /markets/{{ticker}} then, if still no quotes, GET …/orderbook into a shallow copy."""
        out = dict(m)
        tid = str(out.get("ticker") or out.get("market_id") or "").strip()
        if not tid:
            return out
        try:
            detail = self._request("GET", f"/markets/{urllib.parse.quote(tid, safe='')}")
            inner = detail.get("market") if isinstance(detail.get("market"), dict) else detail
            if isinstance(inner, dict):
                for k, v in inner.items():
                    if v is not None and (k not in out or out.get(k) is None):
                        out[k] = v
        except Exception:
            pass
        if _kalshi_row_has_explicit_quotes(out):
            return out
        try:
            ob = self._request("GET", f"/markets/{urllib.parse.quote(tid, safe='')}/orderbook")
            yp, np = _parse_orderbook_yes_no_probs(ob)
            if yp is not None:
                out["yes_bid"] = int(round(yp * 100.0))
            if np is not None:
                out["no_bid"] = int(round(np * 100.0))
        except Exception:
            pass
        return out

    def fetch_kalshi_active_markets(self, *, top_n: int = 200) -> List[Dict[str, Any]]:
        """
        Active pool: single-event binaries (not parlays), ``status=open`` via core tradeable checks,
        minimal activity (``max(volume, volume_24h, open_interest) >= 1``). Includes long-dated
        futures (e.g. NBA championship); **no** close-time horizon — hunts decide timing.

        Fetches each root in ``KALSHI_GOOD_SERIES`` via ``series_ticker=…``, then augments from the
        generic open feed if the merge is sparse.
        """
        now = time.time()
        far_future = now + 3650 * 86400
        merge_cap = 120
        merge: Dict[str, Dict[str, Any]] = {}

        for ser in KALSHI_GOOD_SERIES:
            for row in self.fetch_markets_for_series(ser, limit=merge_cap):
                if not isinstance(row, dict):
                    continue
                tid = str(row.get("ticker") or "").strip()
                if tid:
                    merge[tid] = row

        if len(merge) < 40:
            params: Dict[str, Any] = {"status": "open", "limit": 200}
            try:
                j = self._request("GET", "/markets", params=params)
            except Exception:
                j = self._request("GET", "/markets", params={"limit": 200})
            batch = j.get("markets") or j.get("data") or []
            if isinstance(batch, list):
                for m in batch:
                    if not isinstance(m, dict):
                        continue
                    tid = str(m.get("ticker") or "").strip()
                    if tid:
                        merge.setdefault(tid, m)

        all_markets = list(merge.values())
        if all_markets:
            try:
                raw0 = json.dumps(all_markets[0], indent=2, default=str)
                if len(raw0) > 16000:
                    raw0 = raw0[:16000] + "\n…(truncated)"
                logger.info("Kalshi first market raw: %s", raw0)
            except Exception as exc:
                logger.info("Kalshi first market raw: (unserializable) %s", exc)
        tickers = [str(m.get("ticker", ""))[:12] for m in all_markets[:20]]
        logger.info("Kalshi series merge raw count=%s ticker samples: %s", len(all_markets), tickers)

        for m in all_markets[:5]:
            if not isinstance(m, dict):
                continue
            logger.info(
                "Kalshi raw market: ticker=%s yes_bid=%s yes_ask=%s yes_price=%s volume=%s "
                "volume_24h=%s open_interest=%s close_time=%s status=%s",
                m.get("ticker"),
                m.get("yes_bid"),
                m.get("yes_ask"),
                m.get("yes_price"),
                m.get("volume"),
                m.get("volume_24h"),
                m.get("open_interest"),
                m.get("close_time"),
                m.get("status"),
            )

        rejected_tradeable = 0
        rejected_volume = 0
        rejected_price = 0
        rejected_time = 0
        candidates: List[Dict[str, Any]] = []

        for m in all_markets:
            if not isinstance(m, dict):
                continue
            if not _kalshi_market_tradeable_core(m, now):
                rejected_tradeable += 1
                continue
            if _kalshi_market_volume(m) < 1.0:
                rejected_volume += 1
                continue
            m_row = m
            if not _kalshi_row_has_explicit_quotes(m_row):
                try:
                    m_row = self.enrich_market_with_detail_and_orderbook(m_row)
                except Exception as exc:
                    logger.debug("Kalshi price enrich failed %s: %s", m.get("ticker"), exc)
            ya, na, y_src, n_src = _kalshi_yes_no_from_market_row(m_row)
            if y_src is None and n_src is None:
                rejected_price += 1
                continue
            if ya <= 0 or na <= 0:
                rejected_price += 1
                continue
            candidates.append(m_row)

        logger.info(
            "Kalshi filter breakdown: total=%s rejected_tradeable=%s rejected_volume=%s "
            "rejected_price=%s rejected_time=%s passed=%s",
            len(all_markets),
            rejected_tradeable,
            rejected_volume,
            rejected_price,
            rejected_time,
            len(candidates),
        )

        _log_kalshi_active_market_counts(candidates, now)

        def _sort_key(row: Dict[str, Any]) -> Tuple[float, float]:
            y, _, _, _ = _kalshi_yes_no_from_market_row(row)
            close = _parse_close_timestamp_unix(row)
            imbalance = -abs(y - 0.5)
            close_key = close if close is not None else far_future
            return (imbalance, close_key)

        candidates.sort(key=_sort_key)
        return candidates[:top_n]

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


def fetch_kalshi_active_markets(client: Optional[KalshiClient] = None, *, top_n: int = 200) -> List[Dict[str, Any]]:
    """Module helper — same as :meth:`KalshiClient.fetch_kalshi_active_markets`."""
    c = client or KalshiClient()
    return c.fetch_kalshi_active_markets(top_n=top_n)


def build_kalshi_request_headers(method: str, full_url: str) -> Dict[str, str]:
    """Build Kalshi auth headers from ``KALSHI_API_KEY`` / ``KALSHI_ACCESS_KEY_ID`` (for setup tools)."""
    c = KalshiClient()
    h = c._auth_headers(method, full_url)
    return h


def _kalshi_field_to_probability(val: Any) -> Optional[float]:
    """Kalshi quotes are usually 1–99 (cents); some payloads use 0–1. Return probability in (0,1) or None."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v < 1.0:
        return min(0.99, max(0.01, v))
    if v <= 100.0:
        return min(0.99, max(0.01, v / 100.0))
    return None


def _first_probability_from_fields(m: Dict[str, Any], fields: Tuple[str, ...]) -> Tuple[Optional[float], Optional[str]]:
    for field in fields:
        val = m.get(field)
        p = _kalshi_field_to_probability(val)
        if p is not None:
            return p, field
    return None, None


def _kalshi_yes_no_from_market_row(m: Dict[str, Any]) -> Tuple[float, float, Optional[str], Optional[str]]:
    """
    Best-effort YES/NO probabilities from Kalshi market JSON.
    Tries several field names (cents or unit interval); infers missing side from complement.
    """
    yes_fields = ("yes_bid", "yes_price", "result_yes_price", "last_price", "yes_ask")
    no_fields = ("no_bid", "no_price", "result_no_price", "no_ask")
    yes_p, y_src = _first_probability_from_fields(m, yes_fields)
    no_p, n_src = _first_probability_from_fields(m, no_fields)
    if yes_p is None and no_p is None:
        return 0.5, 0.5, None, None
    if yes_p is None and no_p is not None:
        yes_p = 1.0 - no_p
        y_src = f"inferred_from_{n_src}"
    elif no_p is None and yes_p is not None:
        no_p = 1.0 - yes_p
        n_src = f"inferred_from_{y_src}"
    return yes_p, no_p, y_src, n_src


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

# Multi-leg / parlay tickers — not single-event binaries.
KALSHI_SKIP_PREFIXES: Tuple[str, ...] = (
    "KXMVE",
    "KXMVS",
    "KXMVC",
)

# Prefer these single-event roots (prefix match on ``ticker`` or ``series_ticker``).
# Sports season/championship (KXNBA, …), same-day game lines (KX* TODAY), indices, BTCZ, weather.
KALSHI_GOOD_SERIES: Tuple[str, ...] = (
    "KXNBA",
    "KXNFL",
    "KXMLB",
    "KXNHL",
    "KXTENNIS",
    "KXESPORTS",
    "KXCSGO",
    "KXSOCCER",
    "KXMMA",
    "KXNBATODAY",
    "KXNFLTODAY",
    "KXMLBTODAY",
    "INXD",
    "BTCZ",
    "HIGHTEMP",
    "KXBTCD",
    "KXETHD",
    "NASDAQ",
    "BTC",
    "ETH",
    "FED",
    "CPI",
    "JOBS",
    "TRUMP",
    "CONGRESS",
    "KXBTC",
    "KXBTCZ",
    "KXETH",
    "KXFED",
    "KXCPI",
    "KXJOBS",
    "KXHIGHTEMP",
    "KXINX",
    "KXNDX",
    "KXNASDAQ",
    "KXTRUMP",
    "KXCONGRESS",
)

# Live / same-session sports — polled aggressively for HV near-resolution.
KALSHI_LIVE_SERIES: Tuple[str, ...] = (
    "KXTENNIS",
    "KXESPORTS",
    "KXCSGO",
    "KXNBATODAY",
    "KXMLBTODAY",
    "KXNHLTODAY",
    "KXSOCCER",
    "KXNFLTODAY",
    "KXMMA",
)


def fetch_kalshi_live_sports(client: Optional[KalshiClient] = None) -> List[MarketSnapshot]:
    """
    Open markets in ``KALSHI_LIVE_SERIES`` resolving within ~6 hours, enriched with order book.
    Sorted: highest max(yes,no) first (especially >=95%%), then soonest close.
    """
    c = client or KalshiClient()
    if not c.has_kalshi_credentials():
        return []
    now = time.time()
    horizon = now + 6 * 3600
    merged: Dict[str, Dict[str, Any]] = {}
    for ser in KALSHI_LIVE_SERIES:
        try:
            rows = c.fetch_markets_for_series(ser, limit=100)
        except Exception as exc:
            logger.debug("Kalshi live series %s fetch failed: %s", ser, exc)
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not _kalshi_market_tradeable_core(row, now):
                continue
            end = _parse_close_timestamp_unix(row)
            if end is None or end > horizon or end < now:
                continue
            tid = str(row.get("ticker") or "").strip()
            if tid:
                merged[tid] = row
    snaps: List[MarketSnapshot] = []
    for row in merged.values():
        try:
            snaps.append(map_kalshi_market_to_snapshot(row, now, client=c))
        except Exception:
            continue

    def _sort_key(s: MarketSnapshot) -> Tuple[int, float, float]:
        mx = max(float(s.yes_price), float(s.no_price))
        pri = 0 if mx >= 0.95 else 1
        end = float(getattr(s, "end_date_seconds", None) or 1e12)
        return (pri, -mx, end)

    snaps.sort(key=_sort_key)
    logger.info("Kalshi live sports snapshots: %s", len(snaps))
    return snaps


class KalshiLiveSportsFetcher(BaseOutletFetcher):
    """Narrow Kalshi pool for 60s live scan — tennis / esports / same-day pro sports."""

    outlet_name = "kalshi"

    def __init__(self) -> None:
        self._client = KalshiClient()

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        return fetch_kalshi_live_sports(self._client)


def _kalshi_market_volume(m: Dict[str, Any]) -> float:
    """Best activity signal: max of lifetime volume, 24h volume, and open interest (futures-friendly)."""
    def _f(key: str) -> float:
        val = m.get(key)
        try:
            return float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    return max(_f("volume"), _f("volume_24h"), _f("open_interest"))


def _kalshi_series_key(m: Dict[str, Any]) -> str:
    st = str(m.get("series_ticker") or "").strip().upper()
    if st:
        return st
    tick = str(m.get("ticker") or "")
    if "-" in tick:
        return tick.split("-", 1)[0].upper()
    return tick.upper() or "UNKNOWN"


def _log_kalshi_active_market_counts(markets: List[Dict[str, Any]], now: float) -> None:
    n = len(markets)
    breakdown: Dict[str, int] = {}
    for row in markets:
        k = _kalshi_series_key(row)
        breakdown[k] = breakdown.get(k, 0) + 1
    if _KALSHI_ET is None:
        logger.info(
            "Kalshi active markets found: %s — Resolving today: (n/a) — Resolving tomorrow: (n/a) — Series breakdown: %s",
            n,
            breakdown,
        )
        return
    today_d = datetime.fromtimestamp(now, tz=_KALSHI_ET).date()
    tomorrow_d = today_d + timedelta(days=1)
    n_today = 0
    n_tomorrow = 0
    for row in markets:
        ct = _parse_close_timestamp_unix(row)
        if ct is None:
            continue
        d = datetime.fromtimestamp(ct, tz=_KALSHI_ET).date()
        if d == today_d:
            n_today += 1
        elif d == tomorrow_d:
            n_tomorrow += 1
    logger.info(
        "Kalshi active markets found: %s — Resolving today: %s — Resolving tomorrow: %s — Series breakdown: %s",
        n,
        n_today,
        n_tomorrow,
        breakdown,
    )


def _kalshi_ticker_passes_binary_focus(ticker: str, m: Optional[Dict[str, Any]] = None) -> bool:
    """
    Keep single-event style markets: listed ``KALSHI_GOOD_SERIES`` roots, or any ticker
    that is not a ``KXMV*`` parlay-style product.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return False
    if m:
        ser = str(m.get("series_ticker") or m.get("series_ticker_name") or "").strip().upper()
        if ser and any(ser.startswith(g.upper()) for g in KALSHI_GOOD_SERIES):
            return True
    if any(t.startswith(g.upper()) for g in KALSHI_GOOD_SERIES):
        return True
    if t.startswith("KXMV"):
        return False
    return True


def _kalshi_market_tradeable_core(m: Dict[str, Any], now: float) -> bool:
    """Parlay/ticker focus, settlement, and status — no volume (volume checked separately for logging)."""
    ticker = str(m.get("ticker") or m.get("market_id") or "")
    tu = ticker.upper()
    for prefix in KALSHI_SKIP_PREFIXES:
        if tu.startswith(prefix.upper()):
            return False
    if not _kalshi_ticker_passes_binary_focus(ticker, m):
        return False
    if m.get("settled") or m.get("is_settled"):
        return False
    end = _parse_close_timestamp_unix(m)
    if end is not None and end <= now:
        return False
    st = str(m.get("status", "")).strip().lower()
    if st in _KALSHI_TERMINAL_STATUSES:
        return False
    return True


def _kalshi_market_tradeable(m: Dict[str, Any], now: float) -> bool:
    """Core tradeable checks plus minimal liquidity (lifetime volume, 24h, or open interest)."""
    if not _kalshi_market_tradeable_core(m, now):
        return False
    if _kalshi_market_volume(m) < 1.0:
        return False
    return True


def map_kalshi_market_to_snapshot(
    m: Dict[str, Any],
    now: float,
    *,
    client: Optional[KalshiClient] = None,
) -> MarketSnapshot:
    ticker = str(m.get("ticker") or m.get("market_id") or "")
    row = m
    if client is not None and not _kalshi_row_has_explicit_quotes(m):
        try:
            row = client.enrich_market_with_detail_and_orderbook(dict(m))
        except Exception:
            row = m
    yes_p, no_p, y_src, n_src = _kalshi_yes_no_from_market_row(row)
    vol = float(row.get("volume_24h") or row.get("volume") or 0)
    title = str(row.get("title") or row.get("subtitle") or "")
    end_ts = _parse_close_timestamp_unix(row)
    if y_src:
        logger.debug(
            "Kalshi price source: %s=%.4f no_src=%s=%.4f market=%s",
            y_src,
            yes_p,
            n_src or "-",
            no_p,
            ticker,
        )
    return MarketSnapshot(
        market_id=ticker,
        outlet="kalshi",
        yes_price=yes_p,
        no_price=no_p,
        volume_24h=vol,
        time_to_resolution_seconds=_parse_close_time_seconds(m, now),
        resolution_criteria=title,
        last_price_update_timestamp=now,
        underlying_data_if_available={
            "kalshi_raw": row,
            "kalshi_yes_price_field": y_src,
            "kalshi_no_price_field": n_src,
        },
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
            raw_list = self._client.fetch_kalshi_active_markets(top_n=200)
            if not raw_list:
                raw_list = self._client.fetch_markets_open(limit=500)
                active_rows = [m for m in raw_list if isinstance(m, dict) and _kalshi_market_tradeable(m, now)]
                fb_ticks = [str(m.get("ticker", ""))[:12] for m in active_rows[:20]]
                logger.info("Kalshi ticker samples (open fallback): %s", fb_ticks)
                raw_list = active_rows[:200]
            sample_statuses = [str(m.get("status", "unknown")) for m in raw_list[:20] if isinstance(m, dict)]
            logger.info("Kalshi sample statuses (first %s): %s", len(sample_statuses), sample_statuses)
            logger.info(
                "Kalshi: %s active-pool markets (near-resolution / volume / price band)",
                len(raw_list),
            )
            return [map_kalshi_market_to_snapshot(m, now, client=self._client) for m in raw_list]
        except KalshiAuthError:
            return []
        except Exception as exc:
            logger.warning("Kalshi fetch failed: %s", exc)
            self._client.marked_down = True
            return []
