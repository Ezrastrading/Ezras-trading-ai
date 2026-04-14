"""Polymarket CLOB — https://clob.polymarket.com (L2 HMAC auth per py-clob-client + EIP-712 wallet orders)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import requests
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.outlets.base import BaseOutletFetcher, retry_backoff

if TYPE_CHECKING:
    from trading_ai.shark.models import ExecutionIntent, OrderResult

load_shark_dotenv()

logger = logging.getLogger(__name__)

# Official CLOB signing paths (must match py-clob-client) — HMAC uses path without query string.
CLOB_SIGN_PATH_BALANCE = "/balance-allowance"
CLOB_SIGN_PATH_ORDER = "/order"

# Last working auth method label (set after successful probe).
_POLY_AUTH_WORKING_METHOD: Optional[str] = None


def _token_best_ask_price(tok: Dict[str, Any]) -> Optional[float]:
    """Prefer executable ask; fall back to mid ``price`` if no ask field."""
    for key in ("best_ask", "bestAsk", "ask", "sell", "price", "outcomePrice"):
        v = tok.get(key)
        if v is None:
            continue
        try:
            p = float(v)
            if 0.0 < p < 1.0:
                return p
        except (TypeError, ValueError):
            continue
    return None


def _token_liquidity_field(tok: Dict[str, Any]) -> Optional[float]:
    for key in ("liquidity", "liquidity_num", "liquidityClob"):
        v = tok.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _poly_end_timestamp_seconds(row: Dict[str, Any], now: float) -> Tuple[Optional[float], float]:
    """Parse resolution instant from CLOB / Gamma market row; return (unix_end_ts, seconds_to_resolution)."""
    end_ts: Optional[float] = None
    for key in ("end_time_iso", "endDate", "end_date_iso", "endDateIso"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            try:
                s = v.replace("Z", "+00:00")
                if key == "endDateIso" and len(s) == 10 and s[4] == "-" and s[7] == "-":
                    s = s + "T23:59:59+00:00"
                end_ts = float(datetime.fromisoformat(s).timestamp())
                break
            except ValueError:
                continue
    if end_ts is None:
        ed = row.get("end_date")
        if isinstance(ed, (int, float)):
            ts_f = float(ed)
            if ts_f > 1e12:
                ts_f /= 1000.0
            end_ts = ts_f
    ttr = 86400.0
    if end_ts is not None:
        ttr = max(60.0, end_ts - now)
    return end_ts, ttr


def _is_tradeable_market_dict(row: Dict[str, Any], now: float) -> bool:
    """Tradeable if no parsed end time, or end is strictly in the future (server active/closed flags ignored)."""
    end_ts, _ = _poly_end_timestamp_seconds(row, now)
    if end_ts is not None and end_ts < now:
        return False
    return True


def _gamma_api_base() -> str:
    return (os.environ.get("POLY_GAMMA_API_BASE") or "https://gamma-api.polymarket.com").rstrip("/")


def _parse_polymarket_json_list(val: Any) -> List[Any]:
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip():
        try:
            out = json.loads(val)
            return out if isinstance(out, list) else []
        except json.JSONDecodeError:
            return []
    return []


def fetch_gamma_markets_page(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """Gamma API — curated active market list (recommended over CLOB ``active=`` query params)."""
    base = _gamma_api_base()
    url = f"{base}/markets?closed=false&archived=false&limit={int(limit)}&offset={int(offset)}"
    try:
        r = requests.get(url, headers={"User-Agent": "EzrasShark/1.0"}, timeout=25)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception as exc:
        logger.warning("Polymarket Gamma page fetch failed offset=%s: %s", offset, exc)
    return []


def fetch_gamma_markets(limit_per_page: int = 100) -> List[Dict[str, Any]]:
    """Paginated Gamma ``/markets`` (closed=false, archived=false)."""
    raw_cap = (os.environ.get("EZRAS_POLY_GAMMA_MAX_PAGES") or "").strip()
    if raw_cap.isdigit() and int(raw_cap) == 0:
        max_pages = 50
    elif raw_cap.isdigit():
        max_pages = max(1, int(raw_cap))
    else:
        max_pages = 10
    all_rows: List[Dict[str, Any]] = []
    for page in range(max_pages):
        offset = page * limit_per_page
        batch = fetch_gamma_markets_page(limit_per_page, offset)
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < limit_per_page:
            break
    return all_rows


def _gamma_market_to_clob_like_row(g: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Gamma JSON into a CLOB-shaped dict for shared snapshot + token parsing."""
    cid = str(g.get("conditionId") or g.get("condition_id") or "").strip()
    token_ids = [str(x).strip() for x in _parse_polymarket_json_list(g.get("clobTokenIds")) if str(x).strip()]
    outcomes = _parse_polymarket_json_list(g.get("outcomes"))
    prices_raw = _parse_polymarket_json_list(g.get("outcomePrices"))
    prices_f: List[float] = []
    for p in prices_raw[:2]:
        try:
            prices_f.append(float(p))
        except (TypeError, ValueError):
            prices_f.append(0.5)
    tokens: List[Dict[str, Any]] = []
    for i, tid in enumerate(token_ids[:2]):
        oc = str(outcomes[i]).strip() if i < len(outcomes) else ("Yes" if i == 0 else "No")
        pr = prices_f[i] if i < len(prices_f) else 0.5
        tokens.append({"token_id": tid, "tokenId": tid, "outcome": oc, "price": pr})
    bid = g.get("bestBid")
    ask = g.get("bestAsk")
    vol = g.get("volume24hr") or g.get("volume24hrClob") or g.get("volumeNum") or g.get("volume") or 0
    row: Dict[str, Any] = {
        "condition_id": cid,
        "id": cid,
        "question": g.get("question"),
        "description": g.get("description") or g.get("question"),
        "endDate": g.get("endDate"),
        "end_date_iso": g.get("endDateIso"),
        "end_time_iso": g.get("endDate"),
        "tokens": tokens,
        "volume": vol,
        "volume_24h": vol,
        "volumeNum": vol,
        "question_id": g.get("questionID") or g.get("questionId"),
        "outcome_prices": prices_f,
        "_discovery": "gamma",
    }
    if bid is not None:
        try:
            row["best_bid"] = float(bid)
        except (TypeError, ValueError):
            pass
    if ask is not None:
        try:
            row["best_ask"] = float(ask)
        except (TypeError, ValueError):
            pass
    return row


def _fetch_clob_market_pages_no_filters(fetcher: "PolymarketFetcher", max_pages: Optional[int]) -> List[Dict[str, Any]]:
    """CLOB ``/markets`` without active/closed/archived query filters (filter client-side only)."""
    try:
        from py_clob_client.constants import END_CURSOR
    except ImportError:
        END_CURSOR = "LTE="
    base = fetcher.CLOB_BASE.rstrip("/")
    cursor = "MA=="
    rows: List[Dict[str, Any]] = []
    pages = 0
    while cursor != END_CURSOR:
        if max_pages is not None and pages >= max_pages:
            break
        url = f"{base}/markets?limit=100&next_cursor={urllib.parse.quote(cursor, safe='')}"
        raw = fetcher.http_get_json(url)
        batch = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(batch, list):
            break
        rows.extend([r for r in batch if isinstance(r, dict)])
        pages += 1
        nxt = raw.get("next_cursor") if isinstance(raw, dict) else None
        if not nxt or nxt == END_CURSOR:
            break
        cursor = str(nxt)
    return rows


def fetch_polymarket_clob_market_json(condition_id: str) -> Optional[Dict[str, Any]]:
    """GET ``/markets/{condition_id}`` — public; used to backfill token_ids."""
    cid = (condition_id or "").replace("poly:", "").strip()
    if not cid:
        return None
    base = (os.environ.get("POLY_CLOB_BASE") or "https://clob.polymarket.com").rstrip("/")
    url = f"{base}/markets/{urllib.parse.quote(cid, safe='')}"
    # Public read; do not attach balance-allowance L2 headers (wrong sign path for this URL).
    headers = {"User-Agent": "EzrasShark/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, dict) else None
    except Exception as exc:
        logger.debug("Polymarket CLOB market JSON fetch failed %s: %s", cid, exc)
        return None


def token_ids_from_clob_market_payload(j: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Resolve Yes/No token ids from CLOB ``tokens`` (outcome labels), with index fallback."""
    tokens = j.get("tokens") if isinstance(j.get("tokens"), list) else []
    yes_tid: Optional[str] = None
    no_tid: Optional[str] = None
    for t in tokens:
        if not isinstance(t, dict):
            continue
        oc = str(t.get("outcome") or "").strip().lower()
        tid = str(t.get("token_id") or t.get("tokenId") or "").strip()
        if not tid:
            continue
        if oc == "yes":
            yes_tid = tid
        elif oc == "no":
            no_tid = tid
    if yes_tid and no_tid:
        return yes_tid, no_tid
    if len(tokens) >= 2 and all(isinstance(t, dict) for t in tokens[:2]):
        y = str(tokens[0].get("token_id") or tokens[0].get("tokenId") or "").strip() or None
        n = str(tokens[1].get("token_id") or tokens[1].get("tokenId") or "").strip() or None
        return y, n
    return None, None


def _token_id_log_preview(tid: Optional[str]) -> Optional[str]:
    if not tid:
        return None
    return tid[:8] if len(tid) >= 8 else tid


def enrich_polymarket_snapshot_tokens(m: MarketSnapshot) -> None:
    """If ``yes_token_id`` / ``no_token_id`` missing, merge from CLOB market detail (mutates underlying dict)."""
    if (m.outlet or "").lower() != "polymarket":
        return
    u = dict(m.underlying_data_if_available or {})
    yt = (
        (getattr(m, "yes_token_id", None) or "")
        or (u.get("yes_token_id") or u.get("token_id") or "")
    ).strip()
    nt = ((getattr(m, "no_token_id", None) or "") or (u.get("no_token_id") or "")).strip()
    if yt and nt:
        m.underlying_data_if_available = u
        return
    cid = str(u.get("condition_id") or "").strip() or str(m.market_id).replace("poly:", "").replace("demo-", "")
    if not cid:
        return
    j = fetch_polymarket_clob_market_json(cid)
    if not j:
        return
    y2, n2 = token_ids_from_clob_market_payload(j)
    if y2:
        u["yes_token_id"] = y2
    if n2:
        u["no_token_id"] = n2
    u["token_id"] = u.get("token_id") or y2 or n2 or u.get("token_id")
    m.underlying_data_if_available = u


def ensure_polymarket_intent_token_ids(intent: Any, market: MarketSnapshot) -> bool:
    """
    Ensure Polymarket ``meta`` has token_id(s) for submit. Uses snapshot first, then CLOB GET.
    Returns False if required ids are still missing.
    """
    if (intent.outlet or "").lower() != "polymarket":
        return True
    meta = intent.meta
    if meta.get("pure_arbitrage_dual"):
        yl = dict(meta.get("yes_leg") or {})
        nl = dict(meta.get("no_leg") or {})
        if (yl.get("token_id") or "").strip() and (nl.get("token_id") or "").strip():
            return True
        u = dict(market.underlying_data_if_available or {})
        if getattr(market, "yes_token_id", None):
            u.setdefault("yes_token_id", str(market.yes_token_id).strip())
        if getattr(market, "no_token_id", None):
            u.setdefault("no_token_id", str(market.no_token_id).strip())
        cid = str(u.get("condition_id") or market.market_id.replace("poly:", "")).strip()
        j = fetch_polymarket_clob_market_json(cid)
        if not j:
            logger.warning("Polymarket token fetch: no JSON for condition_id=%s", cid[:16] if cid else None)
            return False
        yt, nt = token_ids_from_clob_market_payload(j)
        logger.info(
            "Polymarket token fetch: yes=%s no=%s condition_id=%s",
            _token_id_log_preview(yt),
            _token_id_log_preview(nt),
            cid[:16] if cid else None,
        )
        if not yt or not nt:
            return False
        yl["token_id"], nl["token_id"] = str(yt), str(nt)
        meta["yes_leg"], meta["no_leg"] = yl, nl
        return True
    tid = (meta.get("token_id") or meta.get("yes_token_id") or "").strip()
    if tid:
        return True
    u = dict(market.underlying_data_if_available or {})
    if getattr(market, "yes_token_id", None):
        u.setdefault("yes_token_id", str(market.yes_token_id).strip())
    if getattr(market, "no_token_id", None):
        u.setdefault("no_token_id", str(market.no_token_id).strip())
    cid = str(meta.get("condition_id") or u.get("condition_id") or market.market_id.replace("poly:", "")).strip()
    j = fetch_polymarket_clob_market_json(cid)
    if not j:
        logger.warning("Polymarket token fetch: no JSON for condition_id=%s", cid[:16] if cid else None)
        return False
    yt, nt = token_ids_from_clob_market_payload(j)
    logger.info(
        "Polymarket token fetch: yes=%s no=%s condition_id=%s",
        _token_id_log_preview(yt),
        _token_id_log_preview(nt),
        cid[:16] if cid else None,
    )
    if not yt or not nt:
        return False
    meta["yes_token_id"] = str(yt)
    meta["no_token_id"] = str(nt)
    side = (intent.side or "yes").lower()
    meta["token_id"] = str(nt if side == "no" else yt)
    return True


def _pad_b64(secret: str) -> str:
    s = secret.strip()
    p = (4 - len(s) % 4) % 4
    return s + ("=" * p if p else "")


def _decode_secret_bytes_for_hmac(api_secret: str) -> bytes:
    """Polymarket API secrets are base64; py-clob uses urlsafe decode — try both."""
    raw = _pad_b64(api_secret)
    try:
        return base64.urlsafe_b64decode(raw)
    except Exception:
        return base64.b64decode(raw)


def build_hmac_signature(
    api_secret: str,
    timestamp: int,
    method: str,
    request_path: str,
    body: Optional[str] = None,
) -> str:
    """L2 HMAC — same construction as py_clob_client.signing.hmac.build_hmac_signature."""
    secret_bytes = _decode_secret_bytes_for_hmac(api_secret)
    message = str(timestamp) + str(method) + str(request_path)
    if body is not None:
        message += str(body).replace("'", '"')
    digest = hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8")


def _wallet_address_hex(wallet_key: str) -> str:
    from eth_account import Account

    pk = wallet_key.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    return Account.from_key("0x" + pk).address


def build_polymarket_l2_headers(
    method: str,
    request_path: str,
    *,
    serialized_body: Optional[str] = None,
) -> Dict[str, str]:
    """
    Official L2 headers (POLY_ADDRESS, POLY_SIGNATURE, …) — matches py-clob-client create_level_2_headers.
    request_path must be the path used for signing (e.g. /balance-allowance or /order), not the full URL.
    """
    load_shark_dotenv()
    wallet_key = (os.getenv("POLY_WALLET_KEY") or "").strip()
    api_key = (os.getenv("POLY_API_KEY") or "").strip()
    api_secret = (os.getenv("POLY_API_SECRET") or "").strip()
    passphrase = (os.getenv("POLY_API_PASSPHRASE") or "").strip()
    if not (wallet_key and api_key and api_secret):
        raise ValueError("POLY_WALLET_KEY, POLY_API_KEY, and POLY_API_SECRET are required for L2 auth")
    ts = int(datetime.now().timestamp())
    sig = build_hmac_signature(api_secret, ts, method, request_path, serialized_body)
    return {
        "POLY_ADDRESS": _wallet_address_hex(wallet_key),
        "POLY_SIGNATURE": sig,
        "POLY_TIMESTAMP": str(ts),
        "POLY_API_KEY": api_key,
        "POLY_PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


def sign_polymarket_request(timestamp_ms: int, api_secret: str) -> str:
    """Ed25519 sign of the timestamp string (ms) — legacy / diagnostic method 1."""
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


def sign_polymarket_request_method2(timestamp_ms: int, api_secret: str, api_key: str) -> str:
    """Ed25519 sign of f\"{timestamp_ms}{api_key}\" — diagnostic method 2."""
    if not (api_secret or "").strip():
        return ""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
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
    msg = f"{timestamp_ms}{api_key}".encode("utf-8")
    sig = private_key.sign(msg)
    return base64.b64encode(sig).decode("ascii")


def _headers_legacy_xpm() -> Dict[str, str]:
    ts = int(time.time() * 1000)
    secret = os.getenv("POLY_API_SECRET", "") or ""
    sig = sign_polymarket_request(ts, secret)
    return {
        "X-PM-Access-Key": os.getenv("POLY_API_KEY", "") or "",
        "X-PM-Timestamp": str(ts),
        "X-PM-Signature": sig,
        "Content-Type": "application/json",
    }


def _headers_method2_xpm() -> Dict[str, str]:
    ts = int(time.time() * 1000)
    secret = os.getenv("POLY_API_SECRET", "") or ""
    api_key = os.getenv("POLY_API_KEY", "") or ""
    sig = sign_polymarket_request_method2(ts, secret, api_key)
    return {
        "X-PM-Access-Key": api_key,
        "X-PM-Timestamp": str(ts),
        "X-PM-Signature": sig,
        "Content-Type": "application/json",
    }


def _balance_allowance_url(host: str) -> str:
    ba_params: Dict[str, str] = {"asset_type": "COLLATERAL"}
    sig = (os.environ.get("POLY_SIGNATURE_TYPE") or "").strip()
    if sig.isdigit():
        ba_params["signature_type"] = sig
    q = urllib.parse.urlencode(ba_params)
    return f"{host.rstrip('/')}{CLOB_SIGN_PATH_BALANCE}?{q}"


def _http_get_balance_probe(url: str, headers: Dict[str, str]) -> Tuple[int, Optional[float], Optional[str]]:
    try:
        hdrs = dict(headers)
        hdrs.setdefault("User-Agent", "EzrasPolymarket/1.0")
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = int(resp.getcode())
            raw = resp.read().decode("utf-8")
            bal: Optional[float] = None
            if raw.strip():
                body = json.loads(raw)
                bal = _extract_balance_from_json(body)
            return code, bal, None
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            return int(e.code), None, (err_body or str(e))[:500]
        except Exception:
            return int(e.code), None, str(e)
    except Exception as e:
        return -1, None, str(e)


def _try_method_1_balance() -> Tuple[int, Optional[float], Optional[str]]:
    host = "https://clob.polymarket.com"
    url = _balance_allowance_url(host)
    return _http_get_balance_probe(url, _headers_legacy_xpm())


def _try_method_2_balance() -> Tuple[int, Optional[float], Optional[str]]:
    host = "https://clob.polymarket.com"
    url = _balance_allowance_url(host)
    return _http_get_balance_probe(url, _headers_method2_xpm())


def _try_method_5_hmac_balance() -> Tuple[int, Optional[float], Optional[str]]:
    """Official L2 HMAC (py-clob-client compatible)."""
    host = "https://clob.polymarket.com"
    url = _balance_allowance_url(host)
    h = build_polymarket_l2_headers("GET", CLOB_SIGN_PATH_BALANCE, serialized_body=None)
    h["User-Agent"] = "EzrasPolymarket/1.0"
    return _http_get_balance_probe(url, h)


def _collateral_balance_params() -> Any:
    from py_clob_client.clob_types import BalanceAllowanceParams

    try:
        from py_clob_client.clob_types import AssetType

        return BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    except Exception:
        return BalanceAllowanceParams(asset_type="COLLATERAL")  # type: ignore[arg-type]


def _try_method_3_sdk_balance() -> Tuple[int, Optional[float], Optional[str]]:
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
    except ImportError:
        return -1, None, "py-clob-client not installed"

    load_shark_dotenv()
    key = (os.getenv("POLY_WALLET_KEY") or "").strip()
    ak = (os.getenv("POLY_API_KEY") or "").strip()
    sec = (os.getenv("POLY_API_SECRET") or "").strip()
    if not (key and ak and sec):
        return -1, None, "missing wallet or API credentials"
    host = (os.getenv("POLY_CLOB_BASE") or "https://clob.polymarket.com").rstrip("/")
    chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
    creds = ApiCreds(
        api_key=ak,
        api_secret=sec,
        api_passphrase=(os.getenv("POLY_API_PASSPHRASE") or "").strip(),
    )
    client = ClobClient(host, chain_id=chain_id, key=key, creds=creds)
    try:
        raw = client.get_balance_allowance(_collateral_balance_params())
        bal = _extract_balance_from_json(raw)
        return 200, bal, None
    except Exception as e:
        return -1, None, str(e)[:500]


def _try_method_4_sdk_derive_balance() -> Tuple[int, Optional[float], Optional[str]]:
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        return -1, None, "py-clob-client not installed"

    load_shark_dotenv()
    key = (os.getenv("POLY_WALLET_KEY") or "").strip()
    if not key:
        return -1, None, "missing POLY_WALLET_KEY"
    host = (os.getenv("POLY_CLOB_BASE") or "https://clob.polymarket.com").rstrip("/")
    chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
    client = ClobClient(host, chain_id=chain_id, key=key)
    try:
        creds = client.create_or_derive_api_creds()
        if creds is None:
            return -1, None, "create_or_derive_api_creds returned None"
        client.set_api_creds(creds)
        raw = client.get_balance_allowance(_collateral_balance_params())
        bal = _extract_balance_from_json(raw)
        return 200, bal, None
    except Exception as e:
        return -1, None, str(e)[:500]


def probe_polymarket_balance_methods() -> Tuple[Optional[float], Optional[str], List[Dict[str, Any]]]:
    """
    Try auth methods in order until HTTP 200 from balance-allowance (or SDK success).
    Returns (balance, winning_method, methods_tried).
    """
    global _POLY_AUTH_WORKING_METHOD
    tried: List[Dict[str, Any]] = []

    def _run(name: str, fn: Any) -> Optional[Tuple[Optional[float], str]]:
        try:
            code, bal, err = fn()
            tried.append({"method": name, "status_code": code, "error": err, "balance": bal})
            if code == 200:
                logger.info("Polymarket auth: method %s working (HTTP/status ok)", name)
                _POLY_AUTH_WORKING_METHOD = name
                return (bal, name)
        except Exception as e:
            tried.append({"method": name, "error": str(e)[:500]})
        return None

    for label, fn in (
        ("1_standard_ed25519_xpm", _try_method_1_balance),
        ("2_ed25519_timestamp_plus_keyid", _try_method_2_balance),
        ("3_py_clob_api_creds", _try_method_3_sdk_balance),
        ("4_py_clob_derive_api_creds", _try_method_4_sdk_derive_balance),
        ("5_official_hmac_l2", _try_method_5_hmac_balance),
    ):
        out = _run(label, fn)
        if out is not None:
            return out[0], out[1], tried

    _POLY_AUTH_WORKING_METHOD = None
    return None, None, tried


def test_polymarket_credentials() -> Dict[str, Any]:
    """
    Run balance probe (methods 1–4, then official HMAC). Surfaces status, balance, and which method worked.
    """
    load_shark_dotenv()
    key_id = (os.getenv("POLY_API_KEY") or "").strip()
    secret_set = bool((os.getenv("POLY_API_SECRET") or "").strip())
    wk = (os.getenv("POLY_WALLET_KEY") or "").strip()
    wa = (os.getenv("POLY_WALLET_ADDRESS") or "").strip()
    wallet_set = bool(wk or wa)

    balance, auth_method, methods_tried = probe_polymarket_balance_methods()
    if auth_method:
        for row in methods_tried:
            if row.get("method") == auth_method and row.get("balance") is not None and balance is None:
                balance = row.get("balance")
        status_code = 200
        err = None
    else:
        last = methods_tried[-1] if methods_tried else {}
        status_code = int(last.get("status_code", -1)) if isinstance(last, dict) else -1
        err = (last.get("error") if isinstance(last, dict) else None) or "all auth methods failed"

    return {
        "status_code": status_code,
        "error": err,
        "balance": balance,
        "key_id_used": key_id,
        "secret_set": secret_set,
        "wallet_set": wallet_set,
        "auth_method": auth_method,
        "methods_tried": methods_tried,
    }


def get_polymarket_headers() -> Dict[str, str]:
    """
    Back-compat: L2 headers for GET /balance-allowance (sign path without query).
    Prefer build_polymarket_l2_headers(method, path, body) for POST /order.
    """
    return build_polymarket_l2_headers("GET", CLOB_SIGN_PATH_BALANCE, serialized_body=None)


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
    Resolve balance using the same probe order as test_polymarket_credentials, then legacy fallbacks.
    """
    load_shark_dotenv()
    bal, method, _ = probe_polymarket_balance_methods()
    if bal is not None:
        logger.info("Polymarket balance: $%.2f (auth method %s)", bal, method)
        return bal

    base = (os.environ.get("POLY_CLOB_BASE") or "https://clob.polymarket.com").rstrip("/")
    fixed_clob = "https://clob.polymarket.com"
    wallet = (os.environ.get("POLY_WALLET_ADDRESS") or "").strip()
    has_pm_auth = bool(
        (os.getenv("POLY_API_KEY") or "").strip()
        and (os.getenv("POLY_API_SECRET") or "").strip()
        and (os.getenv("POLY_WALLET_KEY") or "").strip()
    )

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

    auth_headers: Optional[Dict[str, str]] = None
    if has_pm_auth:
        try:
            auth_headers = build_polymarket_l2_headers("GET", CLOB_SIGN_PATH_BALANCE, serialized_body=None)
        except Exception:
            auth_headers = None

    for url in clob_urls:
        if auth_headers:
            body = _http_get_balance_json(url, auth_headers)
            if body is not None:
                b = _extract_balance_from_json(body)
                if b is not None:
                    logger.info("Polymarket balance: $%.2f (endpoint succeeded: %s)", b, url)
                    return b
        body = _http_get_balance_json(url, None)
        if body is not None:
            b = _extract_balance_from_json(body)
            if b is not None:
                logger.info("Polymarket balance: $%.2f (endpoint succeeded: %s)", b, url)
                return b

    if wallet:
        q = urllib.parse.urlencode({"user": wallet})
        data_url = f"https://data-api.polymarket.com/portfolio?{q}"
        body = _http_get_balance_json(data_url, None)
        if body is not None:
            b = _extract_balance_from_json(body)
            if b is not None:
                logger.info("Polymarket balance: $%.2f (endpoint succeeded: %s)", b, data_url)
                return b

    if not has_pm_auth and not wallet:
        logger.debug("Polymarket balance: set POLY_API_KEY+POLY_API_SECRET+POLY_WALLET_KEY and/or POLY_WALLET_ADDRESS")
    else:
        logger.warning("Polymarket balance: no endpoint returned a parseable USD balance")
    return None


def submit_polymarket_order(intent: "ExecutionIntent") -> "OrderResult":
    """POST signed CLOB order (EIP-712 wallet + L2 API headers)."""
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
        logger.warning("POLY_API_KEY empty — use L2 headers when secret is set")
    return w, k


class PolymarketFetcher(BaseOutletFetcher):
    outlet_name = "polymarket"
    CLOB_BASE = os.environ.get("POLY_CLOB_BASE", "https://clob.polymarket.com")

    def _scan_headers(self) -> Dict[str, str]:
        # Public /markets — no L2 auth required for listing.
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
            page = fetch_gamma_markets_page(limit=1, offset=0)
            if page:
                return True
        except Exception:
            pass
        try:
            base = self.CLOB_BASE.rstrip("/")
            self.http_get_json(f"{base}/markets?limit=1")
            return True
        except Exception:
            return False

    def fetch_balance(self) -> Optional[float]:
        return fetch_polymarket_balance()

    def fetch_markets(self) -> List[MarketSnapshot]:
        return self.fetch_binary_markets()

    def _rows_to_market_snapshots(self, tradeable_rows: List[Dict[str, Any]], now: float) -> List[MarketSnapshot]:
        out: List[MarketSnapshot] = []
        for row in tradeable_rows:
            cid = str(row.get("condition_id") or row.get("id") or "")
            if not cid:
                continue
            tokens = row.get("tokens") if isinstance(row.get("tokens"), list) else []
            yes_tid, no_tid = None, None
            yes, no = 0.5, 0.5
            if len(tokens) >= 2 and all(isinstance(t, dict) for t in tokens[:2]):
                try:
                    y_ask = _token_best_ask_price(tokens[0])
                    n_ask = _token_best_ask_price(tokens[1])
                    y_mid = (
                        float(tokens[0].get("price"))
                        if tokens[0].get("price") is not None
                        else None
                    )
                    n_mid = (
                        float(tokens[1].get("price"))
                        if tokens[1].get("price") is not None
                        else None
                    )
                    yes = y_ask if y_ask is not None else (y_mid if y_mid is not None else 0.5)
                    no = n_ask if n_ask is not None else (n_mid if n_mid is not None else max(1e-6, 1.0 - yes))
                except (TypeError, ValueError):
                    continue
                yes_tid = str(tokens[0].get("token_id") or tokens[0].get("tokenId") or "") or None
                no_tid = str(tokens[1].get("token_id") or tokens[1].get("tokenId") or "") or None
            elif len(tokens) == 1 and isinstance(tokens[0], dict):
                try:
                    y_ask = _token_best_ask_price(tokens[0])
                    yes = y_ask if y_ask is not None else float(tokens[0].get("price") or 0.5)
                    no = max(1e-6, 1.0 - yes)
                except (TypeError, ValueError):
                    continue
                yes_tid = str(tokens[0].get("token_id") or tokens[0].get("tokenId") or "") or None
            else:
                try:
                    op = row.get("outcomePrices") or row.get("outcome_prices")
                    if isinstance(op, list) and len(op) >= 2:
                        yes = float(op[0])
                        no = float(op[1])
                    else:
                        yes = float(row.get("best_ask") or row.get("yes_price") or row.get("price") or 0.5)
                        no = float(
                            row.get("best_ask_no")
                            or row.get("no_price")
                            or max(1e-6, 1.0 - yes)
                        )
                except (TypeError, ValueError):
                    continue
            ly, ln = token_ids_from_clob_market_payload(row)
            if ly:
                yes_tid = ly
            if ln:
                no_tid = ln
            yes = max(1e-6, min(1.0 - 1e-6, float(yes)))
            no = max(1e-6, min(1.0 - 1e-6, float(no)))
            end_ts, ttr = _poly_end_timestamp_seconds(row, now)
            qtext = str(row.get("question") or row.get("title") or "")
            best_y: Optional[float] = None
            best_n: Optional[float] = None
            if len(tokens) >= 2 and all(isinstance(t, dict) for t in tokens[:2]):
                best_y = _token_liquidity_field(tokens[0])
                best_n = _token_liquidity_field(tokens[1])
            u: Dict[str, Any] = {
                "condition_id": cid,
                "yes_token_id": yes_tid,
                "no_token_id": no_tid,
                "token_id": yes_tid or no_tid,
            }
            yt_s = yes_tid if yes_tid else None
            nt_s = no_tid if no_tid else None
            logger.debug(
                "Polymarket tokens: yes=%s no=%s condition_id=%s",
                _token_id_log_preview(yt_s) or "MISSING",
                _token_id_log_preview(nt_s) or "MISSING",
                cid[:12] if cid else "",
            )
            out.append(
                MarketSnapshot(
                    market_id=f"poly:{cid}",
                    outlet=self.outlet_name,
                    yes_price=yes,
                    no_price=no,
                    volume_24h=float(row.get("volume") or row.get("volume_24h") or row.get("volumeNum") or 0),
                    time_to_resolution_seconds=ttr,
                    resolution_criteria=str(row.get("description") or row.get("question") or ""),
                    last_price_update_timestamp=now,
                    underlying_data_if_available=u,
                    canonical_event_key=str(row.get("question_id") or cid),
                    question_text=qtext or None,
                    end_timestamp_unix=end_ts,
                    end_date_seconds=end_ts,
                    best_ask_yes=best_y,
                    best_ask_no=best_n,
                    yes_token_id=yt_s,
                    no_token_id=nt_s,
                )
            )
        return out

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        """Discover markets via Gamma API first; fall back to unfiltered CLOB pagination.

        Tradeability uses **end time only** (``_is_tradeable_market_dict``) — no CLOB ``active=`` / ``closed=`` filters.

        Env ``EZRAS_POLY_GAMMA_MAX_PAGES`` caps Gamma pages (default 10 × 100). ``EZRAS_POLY_CLOB_MAX_PAGES`` caps CLOB
        fallback pages (default 2).
        """
        now = time.time()
        raw_cap = (os.environ.get("EZRAS_POLY_CLOB_MAX_PAGES") or "").strip()
        if raw_cap.isdigit() and int(raw_cap) == 0:
            clob_max_pages: Optional[int] = None
        elif raw_cap.isdigit():
            clob_max_pages = max(1, int(raw_cap))
        else:
            clob_max_pages = 2

        discovery = ""
        tradeable_rows: List[Dict[str, Any]] = []
        raw_fetched = 0

        try:
            gamma_raw = fetch_gamma_markets()
            raw_fetched = len(gamma_raw)
            normalized = [_gamma_market_to_clob_like_row(g) for g in gamma_raw if isinstance(g, dict)]
            normalized = [r for r in normalized if r.get("condition_id")]
            tradeable_rows = [r for r in normalized if _is_tradeable_market_dict(r, now)]
            if tradeable_rows:
                discovery = "gamma-api.polymarket.com"
                logger.info(
                    "Polymarket discovery: Gamma API raw=%s normalized=%s tradeable=%s",
                    len(gamma_raw),
                    len(normalized),
                    len(tradeable_rows),
                )
        except Exception as exc:
            logger.warning("Polymarket Gamma discovery failed: %s", exc)

        if not tradeable_rows:
            try:
                clob_rows = _fetch_clob_market_pages_no_filters(self, clob_max_pages)
                raw_fetched = len(clob_rows)
                tradeable_rows = [r for r in clob_rows if isinstance(r, dict) and _is_tradeable_market_dict(r, now)]
                discovery = "clob.polymarket.com (unfiltered)"
                logger.info(
                    "Polymarket discovery: CLOB unfiltered raw=%s tradeable=%s (Gamma had 0 tradeable or failed)",
                    raw_fetched,
                    len(tradeable_rows),
                )
            except Exception as exc:
                logger.warning("Polymarket CLOB fallback failed: %s", exc)
                return []

        if not tradeable_rows:
            logger.warning(
                "Polymarket: 0 tradeable markets after Gamma + CLOB (discovery=%s raw_rows=%s)",
                discovery or "none",
                raw_fetched,
            )
            return []

        out = self._rows_to_market_snapshots(tradeable_rows, now)
        logger.info(
            "Polymarket discovery source=%s snapshots=%s (end-date filter only)",
            discovery,
            len(out),
        )
        return out
