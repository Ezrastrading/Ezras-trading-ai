"""Macro + market data feeds (FRED, OpenBB) — optional; never blocks trading."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def fetch_fred_macro_snapshot() -> Optional[Dict[str, float]]:
    """
    CPI, Fed funds, GDP, unemployment, VIX proxy — requires FRED_API_KEY.
    Returns None if unavailable (logged).
    """
    key = (os.environ.get("FRED_API_KEY") or "").strip()
    if not key:
        return None
    try:
        from fredapi import Fred

        fred = Fred(api_key=key)
        out: Dict[str, float] = {}
        series = {
            "fed_funds": "FEDFUNDS",
            "cpi_yoy": "CPIAUCSL",
            "gdp_growth": "A191RL1Q225SBEA",
            "unemployment": "UNRATE",
            "vix": "VIXCLS",
        }
        for name, sid in series.items():
            try:
                s = fred.get_series(sid, limit=1)
                if s is not None and len(s) > 0:
                    out[name] = float(s.iloc[-1])  # type: ignore[attr-defined]
            except Exception as exc:
                logger.debug("fred series %s: %s", sid, exc)
        return out if out else None
    except Exception as exc:
        logger.warning("FRED data feed unavailable: %s", exc)
        return None


def fetch_openbb_crypto_sentiment() -> Optional[Dict[str, Any]]:
    """Crypto / sentiment via OpenBB when installed; never raises."""
    try:
        from openbb import obb  # type: ignore

        out: Dict[str, Any] = {"crypto": {}}
        for sym in ("BTC", "ETH", "SOL"):
            try:
                r = obb.crypto.price.historical(symbol=sym, limit=1)  # type: ignore[attr-defined]
                if r is not None and getattr(r, "results", None):
                    row = r.results[0]  # type: ignore[index]
                    out["crypto"][sym] = float(getattr(row, "close", 0) or 0)
            except Exception:
                continue
        return out if out["crypto"] else None
    except Exception as exc:
        logger.warning("OpenBB data feed unavailable: %s", exc)
        return None


def load_combined_data_feeds() -> Dict[str, Any]:
    """Startup / daily refresh bundle — failures are non-fatal."""
    out: Dict[str, Any] = {"fred": None, "openbb": None}
    try:
        out["fred"] = fetch_fred_macro_snapshot()
    except Exception as exc:
        logger.warning("FRED combined load skipped: %s", exc)
    try:
        out["openbb"] = fetch_openbb_crypto_sentiment()
    except Exception as exc:
        logger.warning("OpenBB combined load skipped: %s", exc)
    return out


def enrich_hunt_base_rate(
    base_rate: float,
    market_category: str,
    macro: Optional[Dict[str, Any]],
) -> float:
    """Light touch: high CPI nudges economics / Fed markets upward."""
    if macro is None or not macro.get("fred"):
        return base_rate
    f = macro["fred"]
    cat = (market_category or "").lower()
    br = max(0.01, min(0.99, base_rate))
    if any(x in cat for x in ("fed", "rate", "econom", "macro", "cpi")):
        cpi = f.get("cpi_yoy")
        if cpi is not None and cpi > 300:
            br = min(0.99, br * 1.05)
    return br
