"""
Kalshi Trade API (REST) — execution layer.

Public endpoints work without keys. Balance and orders require RSA-PSS signing:
KALSHI-ACCESS-KEY (KALSHI_API_KEY_ID or KALSHI_API_KEY) plus a PEM private key from
KALSHI_PRIVATE_KEY_PATH or inline KALSHI_API_SECRET. Optional KALSHI_API_PASSPHRASE
for encrypted PEM. On any failure, functions return safe empty / error structures.
"""
from __future__ import annotations

import base64
import logging
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from trading_ai.config import Settings

logger = logging.getLogger(__name__)


def _trade_base(settings: Settings) -> str:
    return str(settings.kalshi_trade_api_base).rstrip("/")


def _sign_path(full_url: str) -> str:
    return urlparse(full_url).path


def _load_private_key(settings: Settings) -> Any:
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        logger.warning("cryptography is not installed; Kalshi signing is unavailable")
        return None

    path = settings.kalshi_private_key_path
    pem_bytes: Optional[bytes] = None
    secret = (settings.kalshi_api_secret or "").strip()
    if secret and "BEGIN" in secret and "PRIVATE KEY" in secret:
        pem_bytes = secret.encode("utf-8")
    elif path is not None and path.is_file():
        with path.open("rb") as f:
            pem_bytes = f.read()
    else:
        return None

    pw = settings.kalshi_api_passphrase
    password = pw.encode("utf-8") if (pw and str(pw).strip()) else None
    try:
        return serialization.load_pem_private_key(
            pem_bytes, password=password, backend=default_backend()
        )
    except Exception:
        logger.exception("Kalshi PEM private key load failed")
        return None


def _sign(private_key: Any, timestamp_ms: str, method: str, path_for_sign: str) -> str:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise RuntimeError("cryptography is not installed") from None

    msg = f"{timestamp_ms}{method.upper()}{path_for_sign.split('?')[0]}".encode("utf-8")
    sig = private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("utf-8")


def _resolve_access_key_id(settings: Settings) -> str:
    """Kalshi API Key ID (UUID); prefer KALSHI_API_KEY_ID, else KALSHI_API_KEY."""
    return (settings.kalshi_api_key_id or settings.kalshi_api_key or "").strip()


def _auth_headers(settings: Settings, method: str, full_url: str) -> Optional[Dict[str, str]]:
    key_id = _resolve_access_key_id(settings)
    pk = _load_private_key(settings)
    if not key_id or pk is None:
        return None
    ts = str(int(__import__("time").time() * 1000))
    path = _sign_path(full_url)
    try:
        sig = _sign(pk, ts, method, path)
    except Exception:
        logger.exception("Kalshi signing failed")
        return None
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


def kalshi_signing_material_status(settings: Settings) -> Dict[str, bool]:
    """Whether access key id and private key (file or KALSHI_API_SECRET PEM) are available."""
    return {
        "access_key_id": bool(_resolve_access_key_id(settings)),
        "private_key": _load_private_key(settings) is not None,
    }


def kalshi_authenticated_smoke(settings: Settings) -> Dict[str, Any]:
    """
    One authenticated GET /portfolio/balance to verify credentials and signing.
    Does not require kalshi_enabled (uses KALSHI_TRADE_API_BASE + keys only).
    """
    out: Dict[str, Any] = {
        "ok": False,
        "http_status": 0,
        "endpoint": "",
        "error": None,
    }
    base = _trade_base(settings)
    rel = "/portfolio/balance"
    full = f"{base}{rel}"
    out["endpoint"] = full
    headers = _auth_headers(settings, "GET", full)
    if not headers:
        out["error"] = "missing_kalshi_credentials"
        return out
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(full, headers=headers)
            out["http_status"] = r.status_code
            if r.status_code == 200:
                out["ok"] = True
            else:
                out["error"] = f"http_{r.status_code}"
    except Exception as exc:
        out["error"] = str(exc)
    return out


def fetch_market_json_public(settings: Settings, ticker: str) -> Optional[Dict[str, Any]]:
    """
    GET /markets/{ticker} (public). Used for dry-run preview without requiring kalshi_enabled.
    """
    if not (ticker or "").strip():
        return None
    base = _trade_base(settings)
    from urllib.parse import quote

    safe = quote(ticker.strip(), safe="")
    url = f"{base}/markets/{safe}"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
        m = data.get("market")
        return dict(m) if isinstance(m, dict) else None
    except Exception:
        logger.exception("Kalshi fetch_market_json_public failed for %s", ticker)
        return None


def build_kalshi_dry_run_order_plan(
    settings: Settings,
    ticker: str,
    side_yes_no: str,
) -> Dict[str, Any]:
    """
    Structured preview of a limit order (same pricing as place_order) without POSTing.
    This CLI never submits orders; pipeline uses place_order when execution is enabled.
    """
    out: Dict[str, Any] = {
        "venue": "kalshi",
        "ticker": (ticker or "").strip(),
        "side": (side_yes_no or "").strip().lower(),
        "size": max(1, int(settings.kalshi_default_order_size)),
        "decision_action": None,
        "yes_limit_price_cents": None,
        "no_limit_price_cents": None,
        "yes_implied_probability": None,
        "error": None,
        "live_trading_enabled_in_settings": bool(settings.kalshi_execution_enabled),
        "cli_never_submits": True,
    }
    syn = out["side"]
    if syn not in ("yes", "no"):
        out["error"] = "invalid_side_use_yes_or_no"
        return out
    m = fetch_market_json_public(settings, ticker)
    if not m:
        out["error"] = "market_not_found_or_unreachable"
        return out
    px = yes_probability_from_market_document(m)
    if px is None:
        out["error"] = "price_unavailable"
        return out
    yes_cents = max(1, min(99, int(round(px * 100))))
    out["yes_implied_probability"] = px
    out["yes_limit_price_cents"] = yes_cents
    if syn == "yes":
        out["decision_action"] = "BUY_YES"
    else:
        no_px = max(0.0, min(1.0, 1.0 - px))
        no_cents = max(1, min(99, int(round(no_px * 100))))
        out["no_limit_price_cents"] = no_cents
        out["decision_action"] = "BUY_NO"
    return out


def get_markets(settings: Settings, *, limit: int = 200, status: str = "open") -> List[Dict[str, Any]]:
    """List markets from Kalshi (public). Returns [] on failure."""
    if not settings.kalshi_enabled:
        return []
    base = _trade_base(settings)
    url = f"{base}/markets?limit={limit}&status={status}"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
        return list(data.get("markets") or [])
    except Exception:
        logger.exception("Kalshi get_markets failed")
        return []


def yes_probability_from_market_document(m: Dict[str, Any]) -> Optional[float]:
    """Map Kalshi market dict to implied YES probability in [0, 1]."""
    try:
        if m.get("last_price_dollars"):
            v = float(str(m["last_price_dollars"]))
            return max(0.0, min(1.0, v))
        yb = m.get("yes_bid_dollars")
        ya = m.get("yes_ask_dollars")
        if yb is not None and ya is not None:
            mid = (float(str(yb)) + float(str(ya))) / 2.0
            return max(0.0, min(1.0, mid))
    except (TypeError, ValueError):
        pass
    return None


def fetch_market_dict(settings: Settings, ticker: str) -> Optional[Dict[str, Any]]:
    """GET /markets/{ticker} JSON `market` object, or None."""
    if not settings.kalshi_enabled or not (ticker or "").strip():
        return None
    base = _trade_base(settings)
    from urllib.parse import quote

    safe = quote(ticker.strip(), safe="")
    url = f"{base}/markets/{safe}"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
        m = data.get("market")
        return dict(m) if isinstance(m, dict) else None
    except Exception:
        logger.exception("Kalshi fetch_market_dict failed for %s", ticker)
        return None


def get_market_price(settings: Settings, market_id: str) -> Optional[float]:
    """
    Kalshi market ticker (pass as market_id). Returns YES-implied probability [0,1] or None.
    """
    if not settings.kalshi_enabled or not (market_id or "").strip():
        return None
    base = _trade_base(settings)
    from urllib.parse import quote

    ticker = market_id.strip()
    safe = quote(ticker, safe="")
    url = f"{base}/markets/{safe}"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
        m = data.get("market") or {}
        return yes_probability_from_market_document(m) if m else None
    except Exception:
        logger.exception("Kalshi get_market_price failed for %s", ticker)
        return None


def list_portfolio_positions(settings: Settings) -> Dict[str, Any]:
    """
    GET /portfolio/positions — open market positions for venue truth sync.

    Returns ``{ok, http_status, positions_raw, error}``. On success, ``positions_raw`` is the
    parsed JSON (Kalshi shape may include ``market_positions`` or ``positions``). Requires auth.
    """
    out: Dict[str, Any] = {"ok": False, "http_status": 0, "positions_raw": None, "error": None}
    if not settings.kalshi_enabled:
        out["error"] = "kalshi_disabled"
        return out
    base = _trade_base(settings)
    rel = "/portfolio/positions"
    full = f"{base}{rel}"
    headers = _auth_headers(settings, "GET", full)
    if not headers:
        out["error"] = "missing_kalshi_credentials"
        return out
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(full, headers=headers)
            out["http_status"] = r.status_code
            if r.status_code != 200:
                out["error"] = f"http_{r.status_code}"
                return out
            data = r.json()
            out["positions_raw"] = data if isinstance(data, dict) else {}
            out["ok"] = True
    except Exception as exc:
        logger.exception("Kalshi list_portfolio_positions failed")
        out["error"] = str(exc)
    return out


def get_balance(settings: Settings) -> Optional[Dict[str, Any]]:
    """GET portfolio balance (cents). Requires API key + private key."""
    if not settings.kalshi_enabled:
        return None
    base = _trade_base(settings)
    rel = "/portfolio/balance"
    full = f"{base}{rel}"
    headers = _auth_headers(settings, "GET", full)
    if not headers:
        logger.warning("Kalshi get_balance: missing credentials")
        return None
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(full, headers=headers)
            r.raise_for_status()
            return dict(r.json())
    except Exception:
        logger.exception("Kalshi get_balance failed")
        return None


def place_order(
    settings: Settings,
    market_id: str,
    side: str,
    size: int,
) -> Dict[str, Any]:
    """
    Place a limit order on Kalshi. `market_id` is the Kalshi ticker.

    `side`: BUY_YES | BUY_NO (maps to Kalshi yes/no contract buy).

    Returns {"ok": bool, "order": ... | None, "error": str | None}.
    """
    out: Dict[str, Any] = {"ok": False, "order": None, "error": None}
    if not settings.kalshi_enabled or not settings.kalshi_execution_enabled:
        out["error"] = "kalshi_execution_disabled"
        return out
    ticker = (market_id or "").strip()
    if not ticker or size < 1:
        out["error"] = "invalid_ticker_or_size"
        return out

    px = get_market_price(settings, ticker)
    if px is None:
        out["error"] = "price_unavailable"
        return out
    # Kalshi limit prices are integer cents 1–99 for YES/NO contracts
    yes_cents = max(1, min(99, int(round(px * 100))))

    base = _trade_base(settings)
    rel = "/portfolio/orders"
    full = f"{base}{rel}"
    headers = _auth_headers(settings, "POST", full)
    if not headers:
        out["error"] = "missing_kalshi_credentials"
        return out
    headers["Content-Type"] = "application/json"

    side_u = side.upper()
    if side_u == "BUY_YES":
        body: Dict[str, Any] = {
            "ticker": ticker,
            "action": "buy",
            "side": "yes",
            "count": int(size),
            "type": "limit",
            "yes_price": yes_cents,
            "client_order_id": str(uuid.uuid4()),
        }
    elif side_u == "BUY_NO":
        no_px = max(0.0, min(1.0, 1.0 - px))
        no_cents = max(1, min(99, int(round(no_px * 100))))
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": "no",
            "count": int(size),
            "type": "limit",
            "no_price": no_cents,
            "client_order_id": str(uuid.uuid4()),
        }
    else:
        out["error"] = "invalid_side"
        return out

    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(full, headers=headers, json=body)
            if r.status_code not in (200, 201):
                out["error"] = f"http_{r.status_code}:{r.text[:500]}"
                return out
            data = r.json()
            out["ok"] = True
            out["order"] = data.get("order")
    except Exception as exc:
        logger.exception("Kalshi place_order failed")
        out["error"] = str(exc)
    return out
