"""Kalshi sports blitz — live game series, 1–10 minute close window (direct series fetch)."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_SERIES = (
    "KXNBA",
    "KXNFL",
    "KXMLB",
    "KXNHL",
    "KXNBATODAY",
    "KXSOC",
    "KXMMA",
    "KXTENNIS",
)


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
    """Open sports markets closing in 60–600s with max(yes,no) ≥ min prob; up to N orders per run."""
    if (os.environ.get("KALSHI_SPORTS_BLITZ_ENABLED") or "false").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return 0

    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.outlets.kalshi import (
        KalshiClient,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
    )
    from trading_ai.shark.reporting import send_telegram
    from trading_ai.shark.state_store import load_capital

    min_prob = _parse_env_float("KALSHI_SPORTS_BLITZ_MIN_PROB", 0.92)
    ttr_min = _parse_env_float("KALSHI_SPORTS_BLITZ_TTR_MIN_SEC", 60.0)
    ttr_max = _parse_env_float("KALSHI_SPORTS_BLITZ_TTR_MAX_SEC", 600.0)
    max_trades = max(1, _parse_env_int("KALSHI_SPORTS_BLITZ_MAX_TRADES", 20))
    budget_pct = max(0.01, min(1.0, _parse_env_float("KALSHI_SPORTS_BLITZ_BUDGET_PCT", 0.40)))
    trade_min = max(0.50, _parse_env_float("KALSHI_SPORTS_BLITZ_MIN_TRADE_USD", 1.00))
    trade_max = max(trade_min, _parse_env_float("KALSHI_SPORTS_BLITZ_MAX_TRADE_USD", 4.00))
    api_limit = max(50, min(500, _parse_env_int("KALSHI_SPORTS_BLITZ_SERIES_LIMIT", 200)))

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        logger.info("Sports blitz skipped — no Kalshi credentials")
        return 0

    now = time.time()
    raw_rows: List[Dict[str, Any]] = []
    for ser in _sports_series():
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
            logger.warning("Sports blitz fetch %s failed: %s", ser, exc)

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
        logger.info("SPORTS BLITZ: 0 markets in %.0f–%.0fs window", ttr_min, ttr_max)
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
        "SPORTS BLITZ: %s markets found, placing %s trades $%.2f each, budget $%.2f",
        len(targets),
        len(selected),
        per_trade,
        budget,
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
