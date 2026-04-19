"""Collect active markets across avenues — read-only snapshot (no execution hooks)."""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

_COINBASE_REST = (
    os.environ.get("COINBASE_PUBLIC_API_BASE", "https://api.exchange.coinbase.com").rstrip("/")
)
# Minimum seconds between snapshot writes (5–10 min band; default mid-band).
_SNAPSHOT_MIN_INTERVAL_SEC = float(
    (os.environ.get("MARKET_INTEL_SNAPSHOT_MIN_SEC") or "400").strip() or "400"
)
_COINBASE_TOP_N = int((os.environ.get("MARKET_INTEL_COINBASE_TOP_N") or "12").strip() or "12")
_KALSHI_MAX_MARKETS = int((os.environ.get("MARKET_INTEL_KALSHI_MAX") or "40").strip() or "40")


def _ssl_ctx() -> ssl.SSLContext:
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
    return ssl.create_default_context()


def _http_get_json(url: str, *, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ezras-market-intel/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _coinbase_usd_products() -> List[Dict[str, Any]]:
    data = _http_get_json(f"{_COINBASE_REST}/products")
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        if str(row.get("quote_currency") or "").upper() != "USD":
            continue
        if str(row.get("status") or "").lower() != "online":
            continue
        pid = str(row.get("id") or "").strip()
        if pid.endswith("-USD"):
            out.append(row)
    return out


def _coinbase_stats(product_id: str) -> Dict[str, Any]:
    try:
        data = _http_get_json(f"{_COINBASE_REST}/products/{urllib.parse.quote(product_id, safe='')}/stats")
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("coinbase stats %s: %s", product_id, exc)
        return {}


def _volatility_from_stats(stats: Dict[str, Any]) -> Optional[float]:
    try:
        last = float(stats.get("last") or 0)
        high = float(stats.get("high") or 0)
        low = float(stats.get("low") or 0)
        if last <= 0 or high <= 0 or low <= 0:
            return None
        return abs(high - low) / last
    except (TypeError, ValueError):
        return None


def _coinbase_intel() -> List[Dict[str, Any]]:
    products = _coinbase_usd_products()
    scored: List[tuple[float, str]] = []
    for row in products:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        v30 = row.get("volume_30_day")
        try:
            vv = float(v30) if v30 is not None else 0.0
        except (TypeError, ValueError):
            vv = 0.0
        scored.append((vv, pid))
    scored.sort(key=lambda x: x[0], reverse=True)
    tickers = [p for _, p in scored[:_COINBASE_TOP_N] if p]
    if not tickers:
        tickers = [str(r.get("id") or "").strip() for r in products if str(r.get("id") or "").endswith("-USD")][
            :_COINBASE_TOP_N
        ]

    out: List[Dict[str, Any]] = []
    for pid in tickers[:_COINBASE_TOP_N]:
        st = _coinbase_stats(pid)
        vol: Optional[float] = None
        try:
            if st.get("volume") is not None:
                vol = float(st["volume"])
        except (TypeError, ValueError):
            vol = None
        try:
            price = float(st.get("last") or 0) or None
        except (TypeError, ValueError):
            price = None
        out.append(
            {
                "symbol": pid,
                "price": price,
                "volume": vol,
                "volatility": _volatility_from_stats(st),
            }
        )
    return out


def _kalshi_title(m: Dict[str, Any]) -> str:
    for k in ("title", "subtitle", "yes_sub_title", "ticker"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(m.get("ticker") or "unknown")


def _kalshi_volume(m: Dict[str, Any]) -> Optional[float]:
    for k in ("volume_24h", "volume", "liquidity"):
        if m.get(k) is None:
            continue
        try:
            return float(m[k])
        except (TypeError, ValueError):
            continue
    return None


def _kalshi_intel() -> List[Dict[str, Any]]:
    try:
        from trading_ai.shark.outlets.kalshi import KalshiClient, _kalshi_yes_no_from_market_row
    except Exception as exc:
        logger.warning("Kalshi intel unavailable (import): %s", exc)
        return []

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        logger.info("Kalshi credentials absent — skipping Kalshi active markets (read-only intel)")
        return []

    try:
        rows = client.fetch_all_open_markets(max_rows=_KALSHI_MAX_MARKETS)
    except Exception as exc:
        logger.warning("Kalshi /markets fetch failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        yp, _, _, _ = _kalshi_yes_no_from_market_row(m)
        out.append(
            {
                "market": _kalshi_title(m),
                "odds": yp,
                "volume": _kalshi_volume(m),
            }
        )
    return out


def _options_intel_stub() -> List[Dict[str, Any]]:
    """No external options feed wired — explicit non-authoritative stub."""
    return [
        {
            "symbol": "SPY",
            "type": "call",
            "volume": None,
            "open_interest": None,
            "note": "stub_no_live_options_feed",
        }
    ]


def market_intelligence_dir() -> Path:
    return ezras_runtime_root() / "market_intelligence"


def active_markets_snapshot_path() -> Path:
    return market_intelligence_dir() / "active_markets_snapshot.json"


def _snapshot_write_ok(path: Path, *, force: bool, min_interval: float) -> bool:
    if force:
        return True
    if not path.exists():
        return True
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True
    return age >= min_interval


def get_active_markets(
    *,
    force_snapshot: bool = False,
    snapshot_min_interval_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Return structured active-market intel. Optionally persist a JSON snapshot under the runtime root.

    Snapshot writes are throttled (default ~6–7 minutes) so this stays suitable for periodic jobs,
    not per-tick use.
    """
    interval = float(snapshot_min_interval_sec if snapshot_min_interval_sec is not None else _SNAPSHOT_MIN_INTERVAL_SEC)
    body: Dict[str, Any] = {
        "coinbase": _coinbase_intel(),
        "kalshi": _kalshi_intel(),
        "options": _options_intel_stub(),
        "collected_at": time.time(),
        "schema": "active_markets_v1",
    }
    snap_path = active_markets_snapshot_path()
    if _snapshot_write_ok(snap_path, force=force_snapshot, min_interval=interval):
        try:
            snap_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = snap_path.with_suffix(snap_path.suffix + ".tmp")
            tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
            tmp.replace(snap_path)
        except OSError as exc:
            logger.warning("market intel snapshot write failed: %s", exc)
    return body
