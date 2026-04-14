"""Polymarket CLOB — https://clob.polymarket.com (Ed25519 API auth + EIP-712 wallet orders)."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.outlets.base import BaseOutletFetcher, retry_backoff

if TYPE_CHECKING:
    from trading_ai.shark.models import ExecutionIntent, OrderResult

load_shark_dotenv()

logger = logging.getLogger(__name__)


def sign_polymarket_request(timestamp_ms: int, api_secret: str) -> str:
    """Ed25519 sign of the timestamp string (ms) using API secret (base64 or hex 32-byte seed)."""
    if not (api_secret or "").strip():
        return ""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        logger.warning("cryptography not installed; Polymarket API signing unavailable")
        return ""
    raw = api_secret.strip()
    secret = raw
    padding = 4 - len(secret) % 4
    if padding != 4:
        secret = secret + "=" * padding
    try:
        key_bytes = base64.b64decode(secret)
    except Exception:
        try:
            key_bytes = base64.urlsafe_b64decode(secret)
        except Exception:
            try:
                key_bytes = bytes.fromhex(raw.replace("0x", ""))
            except Exception:
                return ""
    if len(key_bytes) < 32:
        return ""
    private_key = Ed25519PrivateKey.from_private_bytes(key_bytes[:32])
    msg = str(timestamp_ms).encode("utf-8")
    sig = private_key.sign(msg)
    return base64.b64encode(sig).decode("ascii")


def test_polymarket_credentials() -> Dict[str, Any]:
    """
    GET /balance-allowance with full L2 signed headers (diagnostic for 401 / key–secret mismatch).
    """
    import urllib.error
    import urllib.request

    load_shark_dotenv()
    key_id = (os.getenv("POLY_API_KEY") or "").strip()
    secret_set = bool((os.getenv("POLY_API_SECRET") or "").strip())
    wk = (os.getenv("POLY_WALLET_KEY") or "").strip()
    wa = (os.getenv("POLY_WALLET_ADDRESS") or "").strip()
    wallet_set = bool(wk or wa)

    fixed_clob = "https://clob.polymarket.com"
    ba_params: Dict[str, str] = {"asset_type": "COLLATERAL"}
    sig_t = (os.environ.get("POLY_SIGNATURE_TYPE") or "").strip()
    if sig_t.isdigit():
        ba_params["signature_type"] = sig_t
    q = urllib.parse.urlencode(ba_params)
    url = f"{fixed_clob}/balance-allowance?{q}"

    headers = dict(get_polymarket_headers())
    headers["User-Agent"] = "EzrasSetup/1.0"

    status_code = -1
    error: Optional[str] = None
    balance: Optional[float] = None

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            status_code = int(resp.getcode())
            raw = resp.read().decode("utf-8")
            if raw.strip():
                body = json.loads(raw)
                balance = _extract_balance_from_json(body)
    except urllib.error.HTTPError as e:
        status_code = int(e.code)
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            error = (err_body or str(e))[:500]
        except Exception:
            error = str(e)
    except Exception as e:
        status_code = -1
        error = str(e)

    return {
        "status_code": status_code,
        "error": error,
        "balance": balance,
        "key_id_used": key_id,
        "secret_set": secret_set,
        "wallet_set": wallet_set,
    }


def get_polymarket_headers() -> Dict[str, str]:
    ts = int(time.time() * 1000)
    secret = os.getenv("POLY_API_SECRET", "") or ""
    sig = sign_polymarket_request(ts, secret)
    return {
        "X-PM-Access-Key": os.getenv("POLY_API_KEY", "") or "",
        "X-PM-Timestamp": str(ts),
        "X-PM-Signature": sig,
        "Content-Type": "application/json",
    }


def _http_get_balance_json(url: str, headers: Optional[Dict[str, str]]) -> Optional[Any]:
    try:
        hdrs: Dict[str, str] = {"User-Agent": "EzrasTreasury/1.0"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        logger.debug("Polymarket balance %s → HTTP %s", url, e.code)
        return None
    except Exception as exc:
        logger.debug("Polymarket balance %s → %s", url, exc)
        return None


def _coerce_usdc_balance_field(raw: Any) -> Optional[float]:
    """
    CLOB /balance-allowance returns USDC in micro-units (1e6 per 1 USDC).
    Integer-like values >= 1000 are treated as micro-units; smaller values as USD.
    """
    try:
        if isinstance(raw, str):
            val = float(raw.replace(",", "").strip())
        else:
            val = float(raw)
    except (TypeError, ValueError):
        return None
    if val >= 1000 and abs(val - round(val)) < 1e-9:
        return round(val / 1e6, 2)
    return round(val, 2)


def _extract_balance_from_json(obj: Any) -> Optional[float]:
    """Pull a USD-like balance from JSON (flat dict, nested, or list)."""
    priority_keys = (
        "portfolio_value",
        "balance",
        "usd",
        "amount",
        "available",
        "available_balance",
        "collateral",
        "value",
        "totalValue",
        "total_value",
        "cash",
        "equity",
    )
    if isinstance(obj, (int, float)):
        return round(float(obj), 2)
    if isinstance(obj, dict):
        if "balance" in obj and obj["balance"] is not None:
            coerced = _coerce_usdc_balance_field(obj["balance"])
            if coerced is not None:
                return coerced
        for k in priority_keys:
            if k in obj and obj[k] is not None:
                try:
                    return round(float(obj[k]), 2)
                except (TypeError, ValueError):
                    continue
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("balance", "usd", "portfolio", "amount", "value", "cash", "equity")):
                try:
                    if isinstance(v, (int, float)):
                        return round(float(v), 2)
                    if isinstance(v, str) and v.strip():
                        return round(float(v.replace(",", "")), 2)
                except (TypeError, ValueError):
                    continue
        for v in obj.values():
            nested = _extract_balance_from_json(v)
            if nested is not None:
                return nested
    if isinstance(obj, list):
        for item in obj:
            nested = _extract_balance_from_json(item)
            if nested is not None:
                return nested
    return None


def fetch_polymarket_balance() -> Optional[float]:
    """
    Try CLOB balance URLs (official /balance-allowance, then legacy paths), with L2 auth when set,
    then the same URLs without auth, then data-api portfolio (wallet-only, no auth).
    """
    base = (os.environ.get("POLY_CLOB_BASE") or "https://clob.polymarket.com").rstrip("/")
    fixed_clob = "https://clob.polymarket.com"
    wallet = (os.environ.get("POLY_WALLET_ADDRESS") or "").strip()
    has_pm_auth = bool((os.getenv("POLY_API_KEY") or "").strip() and (os.getenv("POLY_API_SECRET") or "").strip())

    ba_params: Dict[str, str] = {"asset_type": "COLLATERAL"}
    sig = (os.environ.get("POLY_SIGNATURE_TYPE") or "").strip()
    if sig.isdigit():
        ba_params["signature_type"] = sig
    balance_allowance_q = urllib.parse.urlencode(ba_params)

    override = (os.environ.get("POLY_CLOB_BALANCE_URL") or "").strip()
    clob_urls: List[str] = []
    if override:
        clob_urls.append(override if override.startswith("http") else f"{base}/{override.lstrip('/')}")
    clob_urls.extend(
        [
            f"{fixed_clob}/balance-allowance?{balance_allowance_q}",
            f"{fixed_clob}/balance",
            f"{fixed_clob}/accounts/balance",
            f"{fixed_clob}/v1/balance",
        ]
    )

    auth_headers = dict(get_polymarket_headers()) if has_pm_auth else None

    for url in clob_urls:
        if has_pm_auth:
            body = _http_get_balance_json(url, auth_headers)
            if body is not None:
                bal = _extract_balance_from_json(body)
                if bal is not None:
                    logger.info("Polymarket balance: $%.2f (endpoint succeeded: %s)", bal, url)
                    return bal
        body = _http_get_balance_json(url, None)
        if body is not None:
            bal = _extract_balance_from_json(body)
            if bal is not None:
                logger.info("Polymarket balance: $%.2f (endpoint succeeded: %s)", bal, url)
                return bal

    if wallet:
        q = urllib.parse.urlencode({"user": wallet})
        data_url = f"https://data-api.polymarket.com/portfolio?{q}"
        body = _http_get_balance_json(data_url, None)
        if body is not None:
            bal = _extract_balance_from_json(body)
            if bal is not None:
                logger.info("Polymarket balance: $%.2f (endpoint succeeded: %s)", bal, data_url)
                return bal

    if not has_pm_auth and not wallet:
        logger.debug("Polymarket balance: set POLY_API_KEY+POLY_API_SECRET and/or POLY_WALLET_ADDRESS")
    else:
        logger.warning("Polymarket balance: no endpoint returned a parseable USD balance")
    return None


def submit_polymarket_order(intent: "ExecutionIntent") -> "OrderResult":
    """POST signed CLOB order (EIP-712 wallet + Ed25519 API headers)."""
    from trading_ai.shark.polymarket_live import submit_polymarket_order as _submit

    return _submit(intent)


def require_polymarket_credentials_for_live() -> tuple[str, str]:
    """Wallet + API key for live signing when set."""
    load_shark_dotenv()
    w = (os.environ.get("POLY_WALLET_KEY") or "").strip()
    k = (os.environ.get("POLY_API_KEY") or "").strip()
    if not w:
        logger.warning("POLY_WALLET_KEY unset — Polymarket order placement needs wallet key")
    if not k:
        logger.warning("POLY_API_KEY empty — use X-PM headers when secret is set")
    return w, k


class PolymarketFetcher(BaseOutletFetcher):
    outlet_name = "polymarket"
    CLOB_BASE = os.environ.get("POLY_CLOB_BASE", "https://clob.polymarket.com")

    def _scan_headers(self) -> Dict[str, str]:
        if (os.environ.get("POLY_API_KEY") or "").strip() and (os.environ.get("POLY_API_SECRET") or "").strip():
            h = dict(get_polymarket_headers())
            h["User-Agent"] = "EzrasShark/1.0"
            return h
        return {"User-Agent": "EzrasShark/1.0"}

    def http_get_json(self, url: str, timeout: float = 20.0) -> Any:
        headers = self._scan_headers()

        def _req() -> Any:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return retry_backoff(_req)

    def is_healthy(self) -> bool:
        try:
            self.http_get_json(f"{self.CLOB_BASE.rstrip('/')}/markets?limit=1")
            return True
        except Exception:
            return False

    def fetch_balance(self) -> Optional[float]:
        return fetch_polymarket_balance()

    def fetch_markets(self) -> List[MarketSnapshot]:
        return self.fetch_binary_markets()

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        """Active markets via CLOB; authenticated requests unlock restricted markets when configured."""
        try:
            raw = self.http_get_json(f"{self.CLOB_BASE.rstrip('/')}/markets?limit=50")
        except Exception:
            return []
        now = time.time()
        out: List[MarketSnapshot] = []
        rows = raw if isinstance(raw, list) else raw.get("data") if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            return []
        for row in rows[:30]:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("condition_id") or row.get("id") or "")
            if not tid:
                continue
            try:
                yes = float(row.get("yes_price") or row.get("price") or 0.5)
                no = float(row.get("no_price") or (1.0 - yes))
            except (TypeError, ValueError):
                continue
            out.append(
                MarketSnapshot(
                    market_id=f"poly:{tid}",
                    outlet=self.outlet_name,
                    yes_price=yes,
                    no_price=no,
                    volume_24h=float(row.get("volume") or row.get("volume_24h") or 0),
                    time_to_resolution_seconds=float(row.get("time_to_resolution") or 86400.0),
                    resolution_criteria=str(row.get("description") or row.get("question") or ""),
                    last_price_update_timestamp=now,
                )
            )
        return out
