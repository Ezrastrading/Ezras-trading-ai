"""Kalshi crypto vs non-crypto classification — HV blocklist, active pool series, HF scans."""

from __future__ import annotations

import os
from typing import Tuple

# Longest-first matching avoids KXBTCD being swallowed by prefix "KXBTC".
_DEFAULT_CRYPTO_ROOT_PREFIXES: Tuple[str, ...] = (
    "KXBTC15",
    "KXBTCUSD",
    "KXBTCZ",
    "KXBTCD",
    "KXETH15",
    "KXETHD",
    "KXBTC",
    "KXETH",
    "BTCUSD",
    "ETHUSD",
    "BTC15",
    "BTCZ",
    "BTC",
    "ETH",
)


def load_kalshi_crypto_root_prefixes() -> Tuple[str, ...]:
    raw = (os.environ.get("KALSHI_CRYPTO_SERIES") or "").strip()
    if raw:
        parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
        return tuple(sorted(set(parts), key=len, reverse=True))
    return _DEFAULT_CRYPTO_ROOT_PREFIXES


def kalshi_ticker_is_crypto(ticker: str) -> bool:
    """True if market ``ticker`` (or its leading series segment) is crypto per blocklist."""
    t = (ticker or "").strip().upper()
    if not t:
        return False
    root = t.split("-", 1)[0] if "-" in t else t
    for p in load_kalshi_crypto_root_prefixes():
        if root.startswith(p):
            return True
    return False


def kalshi_exclude_crypto_from_hv() -> bool:
    return (os.environ.get("KALSHI_EXCLUDE_CRYPTO_FROM_HV") or "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )


_NC_HF_PRIORITY_SERIES: Tuple[str, ...] = (
    "KXINX",
    "KXNDX",
    "KXNBA",
    "KXNFL",
    "KXMLB",
    "KXNHL",
    "KXFED",
    "KXECON",
    "KXPOL",
    "KXNWS",
    "KXHIGHTEMP",
    "HIGHTEMP",
)


def kalshi_nc_hf_series_to_scan() -> Tuple[str, ...]:
    """Series for the 30s NC HF job: priority list plus the full non-crypto active pool (deduped)."""
    pool = kalshi_non_crypto_series_for_active_pool()
    merged: list[str] = []
    seen: set[str] = set()
    for s in _NC_HF_PRIORITY_SERIES:
        su = s.strip().upper()
        if su and su not in seen:
            seen.add(su)
            merged.append(su)
    for s in pool:
        if s not in seen:
            seen.add(s)
            merged.append(s)
    return tuple(merged)


def kalshi_non_crypto_series_for_active_pool() -> Tuple[str, ...]:
    """
    Series roots scanned in ``fetch_kalshi_active_markets`` (non-crypto HV pool).

    If ``KALSHI_NON_CRYPTO_SERIES`` is set (comma-separated), uses that list only.
    Otherwise: all ``KALSHI_GOOD_SERIES`` entries that are not crypto roots, plus KXPOL / KXECON.
    """
    from trading_ai.shark.outlets.kalshi import KALSHI_GOOD_SERIES

    raw = (os.environ.get("KALSHI_NON_CRYPTO_SERIES") or "").strip()
    if raw:
        return tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    extras = ("KXPOL", "KXECON")
    out: list[str] = []
    seen: set[str] = set()
    for s in KALSHI_GOOD_SERIES:
        su = s.strip().upper()
        if kalshi_ticker_is_crypto(su):
            continue
        if su not in seen:
            seen.add(su)
            out.append(su)
    for e in extras:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return tuple(out)
