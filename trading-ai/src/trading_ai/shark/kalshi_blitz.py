"""Kalshi crypto blitz — BTC/ETH hourly markets, fires once at :54:30 every hour 24/7.

Targets KXBTCD, KXBTC, KXETH, KXETHD markets with TTR 60–360 s (the last 6 minutes
before the hourly close).  At :54:30 these markets are sitting at 90–99 % probability.
Single CronTrigger(minute=54, second=30) — 24 runs per day, all hours, 7 days a week.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default: four core BTC/ETH roots (override with KALSHI_BLITZ_SERIES).
_DEFAULT_SERIES = ("KXBTCD", "KXBTC", "KXETH", "KXETHD")
_BTC_SERIES = frozenset({"KXBTCD", "KXBTC"})
_ETH_SERIES = frozenset({"KXETH", "KXETHD"})


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


def _blitz_series() -> Tuple[str, ...]:
    raw = (os.environ.get("KALSHI_BLITZ_SERIES") or "").strip()
    if raw:
        return tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return _DEFAULT_SERIES


def _bucket(row: Dict[str, Any]) -> Optional[str]:
    st = str(row.get("series_ticker") or "").strip().upper()
    if st in _BTC_SERIES:
        return "btc"
    if st in _ETH_SERIES:
        return "eth"
    tid = str(row.get("ticker") or "").strip().upper()
    if tid.startswith("KXBTC"):
        return "btc"
    if tid.startswith("KXETH"):
        return "eth"
    return None


def run_kalshi_blitz() -> int:
    """Fire at :54:30 — trade BTC/ETH hourly markets closing in the next 6 minutes.

    Targets KXBTCD, KXBTC, KXETH, KXETHD markets with TTR 60–360 s and
    max(yes,no) probability ≥ KALSHI_BLITZ_MIN_PROB (default 90 %).
    Up to KALSHI_BLITZ_MAX_TRADES total trades per run.
    """
    if (os.environ.get("KALSHI_BLITZ_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
        return 0

    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.outlets.kalshi import (
        KalshiClient,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
    )
    from trading_ai.shark.reporting import send_telegram
    from trading_ai.shark.state_store import load_capital

    min_prob = _parse_env_float("KALSHI_BLITZ_MIN_PROB", 0.90)
    ttr_min = _parse_env_float("KALSHI_BLITZ_TTR_MIN_SEC", 60.0)
    ttr_max = _parse_env_float("KALSHI_BLITZ_CLOSE_WINDOW_SEC", 360.0)  # 6-min window default
    max_btc = _parse_env_int("KALSHI_BLITZ_MAX_BTC", 40)
    max_eth = _parse_env_int("KALSHI_BLITZ_MAX_ETH", 40)
    max_total = _parse_env_int("KALSHI_BLITZ_MAX_TRADES", 80)
    budget_pct = max(0.01, min(1.0, _parse_env_float("KALSHI_BLITZ_BUDGET_PCT", 0.80)))
    trade_min = max(0.50, _parse_env_float("KALSHI_BLITZ_MIN_TRADE_USD", 3.00))
    trade_max = max(trade_min, _parse_env_float("KALSHI_BLITZ_MAX_TRADE_USD", 5.00))
    api_limit = max(50, min(500, _parse_env_int("KALSHI_BLITZ_SERIES_LIMIT", 200)))

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        logger.info("Crypto blitz skipped — no Kalshi credentials")
        return 0

    now = time.time()
    series_list = list(_blitz_series())
    raw_rows: List[Dict[str, Any]] = []
    for ser in series_list:
        try:
            j = client._request(
                "GET",
                "/markets",
                params={"status": "open", "limit": api_limit, "series_ticker": ser},
            )
            batch = j.get("markets") or []
            if isinstance(batch, list):
                raw_rows.extend(m for m in batch if isinstance(m, dict))
        except Exception as exc:
            logger.warning("Crypto blitz fetch %s failed: %s", ser, exc)

    targets: List[Dict[str, Any]] = []
    for m in raw_rows:
        try:
            ticker = str(m.get("ticker") or "").strip()
            if not ticker:
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
            close_ts = _parse_close_timestamp_unix(row)
            if close_ts is None:
                continue
            ttr = close_ts - now
            if not (ttr_min <= ttr <= ttr_max):
                continue
            prob = max(y, n)
            if prob < min_prob:
                continue
            bk = _bucket(row)
            if bk is None:
                continue
            side = "yes" if y >= n else "no"
            targets.append(
                {
                    "ticker": ticker,
                    "ttr": ttr,
                    "prob": prob,
                    "side": side,
                    "price": prob,
                    "bucket": bk,
                }
            )
        except Exception:
            continue

    if not targets:
        logger.info("CRYPTO BLITZ: 0 markets in %.0f–%.0fs window at :54:30", ttr_min, ttr_max)
        return 0

    btc = sorted([t for t in targets if t["bucket"] == "btc"], key=lambda x: x["ttr"])[:max_btc]
    eth = sorted([t for t in targets if t["bucket"] == "eth"], key=lambda x: x["ttr"])[:max_eth]
    selected = (btc + eth)[:max_total]

    book = load_capital()
    deployable = effective_capital_for_outlet("kalshi", float(book.current_capital))
    budget = deployable * budget_pct
    n = max(1, len(selected))
    per_trade = max(trade_min, min(trade_max, budget / float(n)))

    if per_trade < trade_min or budget < trade_min:
        logger.info("Crypto blitz skipped — budget $%.2f below min trade $%.2f", budget, trade_min)
        return 0

    n_btc = len([t for t in selected if t["bucket"] == "btc"])
    n_eth = len([t for t in selected if t["bucket"] == "eth"])
    logger.info(
        "CRYPTO BLITZ :54:30 — %s markets found, placing %s trades $%.2f each, "
        "budget $%.2f (BTC %s + ETH %s)",
        len(targets),
        len(selected),
        per_trade,
        budget,
        n_btc,
        n_eth,
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
            logger.warning("Crypto blitz order failed %s: %s", ticker, exc)
            return False, ticker, 0.0

    placed = 0
    deployed = 0.0
    workers = min(32, max(1, len(selected)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ok, _tick, cost in ex.map(_place, selected):
            if ok:
                placed += 1
                deployed += cost

    logger.info(
        "CRYPTO BLITZ DONE: %s/%s filled, $%.2f deployed", placed, len(selected), deployed
    )

    if placed > 0:
        from datetime import datetime, timezone

        hhmm = datetime.now(timezone.utc).strftime("%H:%M")
        earliest_ttr = min(t["ttr"] for t in selected)
        avg_prob = sum(t["prob"] for t in selected) / max(1, len(selected))
        send_telegram(
            f"\U0001f6a8 BLITZ [{hhmm}] — {placed} trades BTC/ETH\n"
            f"  ${deployed:.2f} deployed | closes in ~{int(earliest_ttr / 60)}min\n"
            f"  avg prob={avg_prob*100:.1f}% | {n_btc} BTC + {n_eth} ETH"
        )

    return placed
