"""Polymarket CLOB — https://clob.polymarket.com (Ed25519 API auth + EIP-712 wallet orders)."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
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
    try:
        key_bytes = base64.b64decode(raw)
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


def fetch_polymarket_balance() -> Optional[float]:
    """
    GET /balance on CLOB with auth headers.
    Returns USD balance as float, or None if unconfigured / request fails.
    """
    base = (os.environ.get("POLY_CLOB_BASE") or "https://clob.polymarket.com").rstrip("/")
    if not (os.getenv("POLY_API_KEY") or "").strip() or not (os.getenv("POLY_API_SECRET") or "").strip():
        logger.debug("Polymarket balance: no API key/secret — skip")
        return None
    override = (os.environ.get("POLY_CLOB_BALANCE_URL") or "").strip()
    if override:
        url = override if override.startswith("http") else f"{base}/{override.lstrip('/')}"
    else:
        url = f"{base}/balance"
    try:
        hdrs = dict(get_polymarket_headers())
        hdrs["User-Agent"] = "EzrasTreasury/1.0"
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("Polymarket balance HTTP %s: %s", e.code, e.reason)
        return None
    except Exception as exc:
        logger.warning("Polymarket balance fetch error: %s", exc)
        return None

    bal: float
    if isinstance(body, (int, float)):
        bal = float(body)
    elif isinstance(body, dict):
        bal = float(
            body.get("balance")
            or body.get("usd")
            or body.get("available")
            or body.get("available_balance")
            or body.get("collateral")
            or 0.0
        )
    else:
        bal = 0.0
    bal = round(bal, 2)
    logger.info("Polymarket balance: $%.2f", bal)
    return bal


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
