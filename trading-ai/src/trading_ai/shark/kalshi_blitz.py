"""Kalshi crypto blitz: last minutes before hourly (and 30m) closes — high-conviction market orders."""

from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Kalshi crypto / BTC-ETH hourly & range roots (prefix match via series_ticker).
_DEFAULT_BLITZ_SERIES: Tuple[str, ...] = (
    "KXBTCD",
    "KXETHD",
    "BTCZ",
    "KXBTC",
    "KXBTCZ",
    "KXETH",
    "BTC",
    "ETH",
)


def _blitz_series_list() -> List[str]:
    raw = (os.environ.get("KALSHI_BLITZ_SERIES") or "").strip()
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return list(_DEFAULT_BLITZ_SERIES)


def _close_window_seconds() -> float:
    """Default 540s (9 min) so a :54:30 cron sees markets resolving at :00 (≈330s away) with generous slack."""
    raw = (os.environ.get("KALSHI_BLITZ_CLOSE_WINDOW_SEC") or "540").strip() or "540"
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 360.0


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


def run_kalshi_blitz() -> int:
    """Fetch crypto markets closing soon, filter by edge prob, split budget, fire market orders.

    Returns number of trades placed.  Enabled by default (KALSHI_BLITZ_ENABLED=true).
    """
    if (os.environ.get("KALSHI_BLITZ_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
        return 0

    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.execution_live import monitor_position
    from trading_ai.shark.kalshi_limits import (
        count_kalshi_open_positions,
        kalshi_max_open_positions_from_env,
        kalshi_min_position_usd,
    )
    from trading_ai.shark.models import HuntType, OpenPosition
    from trading_ai.shark.outlets.kalshi import (
        KalshiClient,
        _kalshi_market_tradeable_core,
        _kalshi_market_volume,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
    )
    from trading_ai.shark.reporting import send_telegram
    from trading_ai.shark.state_store import load_capital

    min_prob = _parse_env_float("KALSHI_BLITZ_MIN_PROB", 0.90)
    max_trades = max(1, _parse_env_int("KALSHI_BLITZ_MAX_TRADES", 30))
    budget_pct = max(0.01, min(1.0, _parse_env_float("KALSHI_BLITZ_BUDGET_PCT", 0.50)))
    # Per-trade size clamps: $1–$4 by default (small, many trades)
    blitz_trade_min = max(0.50, _parse_env_float("KALSHI_BLITZ_MIN_TRADE_USD", 1.00))
    blitz_trade_max = max(blitz_trade_min, _parse_env_float("KALSHI_BLITZ_MAX_TRADE_USD", 4.00))
    window_sec = _close_window_seconds()

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        logger.info("Kalshi blitz skipped — no credentials")
        return 0

    book = load_capital()
    # effective_capital_for_outlet already applies the 20% cash reserve.
    # Blitz uses KALSHI_BLITZ_BUDGET_PCT (default 50%) of that deployable slice.
    deployable = effective_capital_for_outlet("kalshi", float(book.current_capital))
    blitz_budget = deployable * budget_pct
    if blitz_budget < blitz_trade_min:
        logger.info(
            "Kalshi blitz skipped — budget $%.2f below min trade $%.2f",
            blitz_budget,
            blitz_trade_min,
        )
        return 0

    now = time.time()
    merged: Dict[str, Dict[str, Any]] = {}
    for ser in _blitz_series_list():
        try:
            rows = client.fetch_markets_for_series(ser, limit=120)
        except Exception as exc:
            logger.debug("Kalshi blitz series %s: %s", ser, exc)
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("ticker") or "").strip()
            if tid:
                merged[tid] = row

    candidates: List[Tuple[Dict[str, Any], float, float, str]] = []
    for m in merged.values():
        if not _kalshi_market_tradeable_core(m, now):
            continue
        if _kalshi_market_volume(m) < 1.0:
            continue
        end = _parse_close_timestamp_unix(m)
        if end is None:
            continue
        ttr = end - now
        if ttr <= 0 or ttr > window_sec:
            continue
        try:
            row = client.enrich_market_with_detail_and_orderbook(dict(m))
        except Exception:
            row = m
        y, n, _, _ = _kalshi_yes_no_from_market_row(row)
        mx = max(y, n)
        if mx < min_prob:
            continue
        side = "yes" if y >= n else "no"
        px = y if side == "yes" else n
        candidates.append((row, float(end), float(px), side))

    candidates.sort(key=lambda x: x[1])
    max_open = kalshi_max_open_positions_from_env()
    open_n = count_kalshi_open_positions()
    slots = max(0, max_open - open_n)
    n_take = min(len(candidates), max_trades, slots)
    if n_take <= 0:
        logger.info(
            "BLITZ MODE: found %s crypto markets closing in 5min, placing 0 trades (cap/slots open=%s/%s)",
            len(candidates),
            open_n,
            max_open,
        )
        return 0

    selected = candidates[:n_take]
    # Per-trade USD: evenly distribute budget, clamped to blitz-specific [$1, $4] band
    per_usd = blitz_budget / float(n_take)
    per_usd = max(blitz_trade_min, min(blitz_trade_max, per_usd))
    if per_usd < kalshi_min_position_usd():
        logger.info("Kalshi blitz skipped — per-trade $%.2f below minimum", per_usd)
        return 0

    earliest_close = min(s[1] for s in selected)
    close_disp = datetime.fromtimestamp(earliest_close, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _place(item: Tuple[Dict[str, Any], float, float, str]) -> Tuple[bool, str, float, Optional[OpenPosition]]:
        m, _end, px, side = item
        ticker = str(m.get("ticker") or "").strip()
        if not ticker:
            return False, "", 0.0, None
        yy, nn, _, _ = _kalshi_yes_no_from_market_row(m)
        edge = max(0.0, max(yy, nn) - min_prob)
        cnt = max(1, int(per_usd / max(px, 0.01)))
        try:
            res = client.place_order(
                ticker=ticker,
                side=side,
                count=cnt,
                order_type="market",
            )
        except Exception as exc:
            logger.warning("Blitz order failed %s: %s", ticker, exc)
            return False, ticker, 0.0, None
        fp = float(res.filled_price or 0.0)
        fs = float(res.filled_size or 0.0)
        if fs <= 0 and res.raw:
            o = res.raw.get("order") if isinstance(res.raw.get("order"), dict) else {}
            fs = float(res.raw.get("filled_count", 0) or o.get("filled_count", 0) or 0)
        if fp <= 0 and px > 0:
            fp = px
        notional = (fp * fs) if fs > 0 else 0.0
        pos = None
        if fs > 0:
            pos = OpenPosition(
                position_id=f"blitz-{uuid.uuid4().hex[:12]}",
                outlet="kalshi",
                market_id=ticker,
                side=side,
                entry_price=fp if fp > 0 else px,
                shares=fs,
                notional_usd=float(notional),
                order_id=str(res.order_id or ""),
                opened_at=time.time(),
                strategy_key="kalshi_blitz",
                hunt_types=[HuntType.KALSHI_NEAR_CLOSE.value],
                market_category="crypto_blitz",
                expected_edge=edge,
            )
        return fs > 0, ticker, float(notional if fs > 0 else 0.0), pos

    total_budget = per_usd * n_take
    logger.info(
        "BLITZ MODE ACTIVATED: found %s crypto markets closing in 5min, placing %s trades with budget $%.2f",
        len(candidates),
        n_take,
        total_budget,
    )

    placed = 0
    deployed = 0.0
    ok_tickers: List[str] = []
    pending_positions: List[OpenPosition] = []
    with ThreadPoolExecutor(max_workers=min(16, n_take)) as ex:
        futs = [ex.submit(_place, s) for s in selected]
        for fut in as_completed(futs):
            ok, tick, usd, pos = fut.result()
            if ok and pos is not None:
                placed += 1
                deployed += usd
                ok_tickers.append(tick)
                pending_positions.append(pos)
    for pos in pending_positions:
        try:
            monitor_position(pos, save=True)
        except Exception as exc:
            logger.warning("Blitz monitor_position failed %s: %s", pos.market_id, exc)

    uniq_markets = len(set(ok_tickers))

    if placed > 0:
        send_telegram(
            f"\U0001f6a8 BLITZ \u2014 placing {placed} trades on {uniq_markets} markets, ${deployed:.2f} deployed"
        )
        logger.info("BLITZ done: %s/%s filled, $%.2f deployed, close at %s", placed, n_take, deployed, close_disp)
    else:
        logger.info("BLITZ done: 0/%s filled (all orders rejected/failed)", n_take)

    return placed
