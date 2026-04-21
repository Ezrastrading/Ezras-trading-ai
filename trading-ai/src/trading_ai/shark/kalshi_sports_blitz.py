"""Kalshi sports/live-game blitz — scans GAME-specific series + MVE-filtered broad scan.

Strategy:
- Primary: scan individual-game series (KXMLBGAME, KXNBAGAME, KXNHLGAME, etc.) directly.
  These have 600k+ volume and are the real tradeable game markets.
- Secondary: broad open scan filtered to exclude MVE/parlay tickers (junk with 1c prices).
- TTR default 60-3600s: catches live games in their final hour when one team is heavily favored.
  Raise KALSHI_SPORTS_BLITZ_TTR_MAX_SEC to 86400 for same-day pre-game trades.
- Min prob 85%, min volume 50.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Game-specific series (NOT championship/season series which have zero short-window markets).
# KXMLBGAME = individual MLB game winner (closes when game ends, typically 600k+ volume).
_DEFAULT_SERIES: Tuple[str, ...] = (
    "KXMLBGAME",
    "KXNBAGAME",
    "KXNHLGAME",
    "KXSOCGAME",
    "KXMMGAME",
    "KXNBATODAY",
    "KXTENNIS",
    "KXNBA",
    "KXNFL",
    "KXMMA",
)

# Ticker substrings that identify MVE/parlay markets — 1c prices, essentially untradeable.
_MVE_SKIP: Tuple[str, ...] = ("MVE", "MULTIGAME", "CROSSCATEGORY", "MULTILEG", "EXTENDED")


def _is_mve(ticker: str) -> bool:
    t = ticker.upper()
    return any(p in t for p in _MVE_SKIP)


def _parse_env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _sports_series() -> Tuple[str, ...]:
    raw = (os.environ.get("KALSHI_SPORTS_BLITZ_SERIES") or "").strip()
    if raw:
        return tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return _DEFAULT_SERIES


def run_kalshi_sports_blitz() -> int:
    """Game-series scan + MVE-filtered broad scan; TTR 60-3600s (live game final hour)."""
    if (os.environ.get("KALSHI_SPORTS_BLITZ_ENABLED") or "false").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return 0

    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.kalshi_crypto import kalshi_ticker_is_crypto
    from trading_ai.shark.outlets.kalshi import (
        KalshiClient,
        _kalshi_market_volume,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
    )
    from trading_ai.shark.reporting import send_telegram
    from trading_ai.shark.state_store import load_capital

    min_prob = _parse_env_float("KALSHI_SPORTS_BLITZ_MIN_PROB", 0.85)
    ttr_min = _parse_env_float("KALSHI_SPORTS_BLITZ_TTR_MIN_SEC", 60.0)
    ttr_max = _parse_env_float("KALSHI_SPORTS_BLITZ_TTR_MAX_SEC", 3600.0)
    min_volume = _parse_env_float("KALSHI_SPORTS_BLITZ_MIN_VOLUME", 50.0)
    max_trades = max(1, _parse_env_int("KALSHI_SPORTS_BLITZ_MAX_TRADES", 20))
    budget_pct = max(0.01, min(1.0, _parse_env_float("KALSHI_SPORTS_BLITZ_BUDGET_PCT", 0.40)))
    trade_min = max(0.50, _parse_env_float("KALSHI_SPORTS_BLITZ_MIN_TRADE_USD", 1.00))
    trade_max = max(trade_min, _parse_env_float("KALSHI_SPORTS_BLITZ_MAX_TRADE_USD", 4.00))
    api_limit = max(50, min(500, _parse_env_int("KALSHI_SPORTS_BLITZ_SERIES_LIMIT", 200)))
    broad_max = max(500, min(10000, _parse_env_int("KALSHI_SPORTS_BLITZ_OPEN_SCAN_MAX_ROWS", 2000)))

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        logger.info("Sports blitz skipped — no Kalshi credentials")
        return 0

    now = time.time()
    merged: Dict[str, Dict[str, Any]] = {}

    # ── 1. Series-targeted scan (game-specific series have real prices + volume) ──
    n_series_found = 0
    for ser in _sports_series():
        try:
            j = client._request(
                "GET",
                "/markets",
                params={"status": "open", "limit": api_limit, "series_ticker": ser},
            )
            batch = j.get("markets") or []
            if isinstance(batch, list):
                for m in batch:
                    if not isinstance(m, dict):
                        continue
                    tid = str(m.get("ticker") or "").strip()
                    if tid and not _is_mve(tid):
                        merged[tid] = m
                        n_series_found += 1
        except Exception as exc:
            logger.debug("Sports blitz series %s: %s", ser, exc)

    # ── 2. Broad open scan (skip MVE parlays which dominate the feed) ─────────
    n_broad = 0
    try:
        broad_rows = client.fetch_all_open_markets(max_rows=broad_max)
        for m in broad_rows:
            if not isinstance(m, dict):
                continue
            tid = str(m.get("ticker") or "").strip()
            if tid and not _is_mve(tid) and tid not in merged:
                merged[tid] = m
                n_broad += 1
        logger.debug("Sports blitz broad scan: %s non-MVE rows from %s fetched", n_broad, len(broad_rows))
    except Exception as exc:
        logger.warning("Sports blitz broad scan failed: %s", exc)

    targets: List[Dict[str, Any]] = []
    for m in merged.values():
        try:
            ticker = str(m.get("ticker") or "").strip()
            if not ticker or kalshi_ticker_is_crypto(ticker) or _is_mve(ticker):
                continue
            row = dict(m)
            y, n, _, _ = _kalshi_yes_no_from_market_row(row)
            if y <= 0 or n <= 0:
                try:
                    row = client.enrich_market_with_detail_and_orderbook(dict(m))
                    y, n, _, _ = _kalshi_yes_no_from_market_row(row)
                except Exception:
                    continue
            if y <= 0 or n <= 0:
                continue
            if _kalshi_market_volume(row) < min_volume:
                continue
            close_ts = _parse_close_timestamp_unix(row)
            if close_ts is None:
                continue
            ttr = close_ts - now
            if not (ttr_min <= ttr <= ttr_max):
                continue
            prob = max(y, n)
            if prob < min_prob:
                continue
            side = "yes" if y >= n else "no"
            targets.append(
                {
                    "ticker": ticker,
                    "ttr": ttr,
                    "prob": prob,
                    "side": side,
                    "price": prob,
                }
            )
        except Exception:
            continue

    if not targets:
        logger.info(
            "SPORTS BLITZ: 0 markets (TTR %.0f–%.0fs, vol≥%.0f, prob≥%.0f%%) — "
            "series=%d broad=%d merged=%d",
            ttr_min, ttr_max, min_volume, min_prob * 100.0,
            n_series_found, n_broad, len(merged),
        )
        return 0

    targets.sort(key=lambda x: x["ttr"])
    selected = targets[:max_trades]

    book = load_capital()
    deployable = effective_capital_for_outlet("kalshi", float(book.current_capital))
    budget = deployable * budget_pct
    n = max(1, len(selected))
    per_trade = max(trade_min, min(trade_max, budget / float(n)))

    if per_trade < trade_min or budget < trade_min:
        logger.info("Sports blitz skipped — budget $%.2f below min trade $%.2f", budget, trade_min)
        return 0

    logger.info(
        "SPORTS BLITZ: %s markets, placing %s trades $%.2f each, budget $%.2f (series=%d broad=%d)",
        len(targets), len(selected), per_trade, budget, n_series_found, n_broad,
    )

    def _place(t: Dict[str, Any]) -> Tuple[bool, str, float]:
        ticker = str(t["ticker"])
        px = max(float(t["price"]), 0.01)
        cnt = max(1, int(per_trade / px))
        try:
            res = client.place_order(ticker=ticker, side=t["side"], count=cnt, action="buy")
            fs = float(res.filled_size or 0.0)
            fp = float(res.filled_price or 0.0)
            cost = fs * fp if fs > 0 and fp > 0 else 0.0
            ok = fs > 0 and (res.success is not False)
            return ok, ticker, cost
        except Exception as exc:
            logger.warning("Sports blitz order failed %s: %s", ticker, exc)
            return False, ticker, 0.0

    placed = 0
    deployed = 0.0
    workers = min(20, max(1, len(selected)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ok, _tick, cost in ex.map(_place, selected):
            if ok:
                placed += 1
                deployed += cost

    logger.info("SPORTS BLITZ DONE: %s/%s filled, $%.2f deployed", placed, len(selected), deployed)

    if placed > 0:
        send_telegram(f"🏀 SPORTS BLITZ — {placed} trades, ${deployed:.2f}")

    return placed
