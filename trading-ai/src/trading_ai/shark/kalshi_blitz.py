"""Kalshi crypto blitz — BTC/ETH 15-minute Kalshi windows (Mon–Fri ~9am–5pm ET).

Targets KXBTCD, KXBTC, KXETH, KXETHD with TTR 60–360 s (≈6 minutes to close).
Scheduler: CronTrigger every 15 minutes during US session + 120 s backup interval.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
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


def _bid_dollars_float(row: Dict[str, Any], key: str) -> float:
    """Parse Kalshi ``*_dollars`` field to float; missing or bad → 0.0."""
    v = row.get(key)
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _blitz_zero_fill_streak_path() -> Path:
    from trading_ai.governance.storage_architecture import shark_state_path

    return shark_state_path("blitz_zero_fill_streak.json")


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
    """Trade BTC/ETH crypto series markets closing in the next ~6 minutes (TTR window).

    Targets KXBTCD, KXBTC, KXETH, KXETHD using **yes_bid_dollars** / **no_bid_dollars** only
    (no inferred mids). Side must meet KALSHI_BLITZ_MIN_PROB (default 90 %).
    """
    if (os.environ.get("KALSHI_BLITZ_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
        return 0

    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.outlets.kalshi import KalshiClient, _parse_close_timestamp_unix
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

    dry_run = (os.environ.get("KALSHI_BLITZ_DRY_RUN") or "false").strip().lower() == "true"

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
            yes_bid = _bid_dollars_float(row, "yes_bid_dollars")
            no_bid = _bid_dollars_float(row, "no_bid_dollars")
            if yes_bid <= 0.0 and no_bid <= 0.0:
                try:
                    row = client.enrich_market_with_detail_and_orderbook(dict(m))
                    yes_bid = _bid_dollars_float(row, "yes_bid_dollars")
                    no_bid = _bid_dollars_float(row, "no_bid_dollars")
                except Exception:
                    continue
            if yes_bid <= 0.0 and no_bid <= 0.0:
                continue

            if yes_bid >= min_prob:
                side = "yes"
                prob = yes_bid
            elif no_bid >= min_prob:
                side = "no"
                prob = no_bid
            else:
                continue

            if yes_bid > 0.0 and no_bid > 0.0:
                yes_cents = max(1, min(99, int(round(yes_bid * 100))))
                no_cents = max(1, min(99, int(round(no_bid * 100))))
            elif yes_bid > 0.0:
                yes_cents = max(1, min(99, int(round(yes_bid * 100))))
                no_cents = max(1, min(99, int(round((1.0 - yes_bid) * 100))))
            else:
                no_cents = max(1, min(99, int(round(no_bid * 100))))
                yes_cents = max(1, min(99, int(round((1.0 - no_bid) * 100))))

            close_ts = _parse_close_timestamp_unix(row)
            if close_ts is None:
                continue
            ttr = close_ts - now
            if not (ttr_min <= ttr <= ttr_max):
                continue
            bk = _bucket(row)
            if bk is None:
                continue
            targets.append(
                {
                    "ticker": ticker,
                    "ttr": ttr,
                    "prob": prob,
                    "side": side,
                    "price": prob,
                    "bucket": bk,
                    "yes_cents": yes_cents,
                    "no_cents": no_cents,
                    "yes_bid": yes_bid,
                    "no_bid": no_bid,
                }
            )
        except Exception:
            continue

    if not targets:
        logger.info("CRYPTO BLITZ: 0 markets in %.0f–%.0fs TTR window", ttr_min, ttr_max)
        return 0

    if dry_run:
        for t in targets[:5]:
            logger.info(
                "DRY RUN target: ticker=%s side=%s prob=%.2f yes_bid=%.4f no_bid=%.4f ttr=%.0fmin",
                t["ticker"],
                t["side"],
                float(t["prob"]),
                float(t.get("yes_bid", 0.0)),
                float(t.get("no_bid", 0.0)),
                float(t["ttr"]) / 60.0,
            )
        return len(targets)

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
        "CRYPTO BLITZ — %s markets found, placing %s trades $%.2f each, "
        "budget $%.2f (BTC %s + ETH %s)",
        len(targets),
        len(selected),
        per_trade,
        budget,
        n_btc,
        n_eth,
    )

    batch_size = max(1, _parse_env_int("KALSHI_BLITZ_BATCH_SIZE", 5))
    batch_delay = max(0.0, _parse_env_float("KALSHI_BLITZ_BATCH_DELAY_SEC", 0.5))
    max_workers = max(1, min(5, _parse_env_int("KALSHI_BLITZ_MAX_WORKERS", 5)))
    retry_429_sleep = max(0.0, _parse_env_float("KALSHI_BLITZ_429_RETRY_SLEEP_SEC", 2.0))
    fill_to = max(0.5, _parse_env_float("KALSHI_BLITZ_FILL_TIMEOUT_SEC", 30.0))
    price_bump_cents = max(0, _parse_env_int("KALSHI_BLITZ_PRICE_BUMP_CENTS", 1))

    def _place(t: Dict[str, Any]) -> Tuple[bool, str, float]:
        ticker = str(t["ticker"])
        px = max(float(t["price"]), 0.01)
        cnt = max(1, int(per_trade / px))

        def _do() -> Tuple[bool, str, float]:
            side_l = str(t["side"]).strip().lower()
            side_cents = int(t["yes_cents"]) if side_l == "yes" else int(t["no_cents"])
            side_cents = max(1, min(99, side_cents))
            res = client.place_order(
                ticker=ticker,
                side=t["side"],
                count=cnt,
                action="buy",
                order_type="market",
                side_price_cents=side_cents,
                fill_timeout_sec=fill_to,
                min_order_prob=min_prob,
                blitz_retry_bump_cents=price_bump_cents,
            )
            fs = float(res.filled_size or 0.0)
            fp = float(res.filled_price or 0.0)
            cost = fs * fp if fs > 0 and fp > 0 else 0.0
            ok = fs > 0 and (res.success is not False)
            return ok, ticker, cost

        try:
            return _do()
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "429" in msg or "too many requests" in msg:
                logger.warning("Crypto blitz 429 on %s — one retry after %.1fs", ticker, retry_429_sleep)
                time.sleep(retry_429_sleep)
                try:
                    return _do()
                except Exception as exc2:
                    logger.warning("Crypto blitz order failed %s after 429 retry: %s", ticker, exc2)
                    return False, ticker, 0.0
            logger.warning("Crypto blitz order failed %s: %s", ticker, exc)
            return False, ticker, 0.0
        except Exception as exc:
            logger.warning("Crypto blitz order failed %s: %s", ticker, exc)
            return False, ticker, 0.0

    placed = 0
    deployed = 0.0
    for batch_start in range(0, len(selected), batch_size):
        chunk = selected[batch_start : batch_start + batch_size]
        workers = min(max_workers, max(1, len(chunk)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for ok, _tick, cost in ex.map(_place, chunk):
                if ok:
                    placed += 1
                    deployed += cost
        if batch_start + batch_size < len(selected):
            time.sleep(batch_delay)

    logger.info(
        "CRYPTO BLITZ DONE: %s/%s filled, $%.2f deployed", placed, len(selected), deployed
    )

    streak_path = _blitz_zero_fill_streak_path()
    try:
        streak_path.parent.mkdir(parents=True, exist_ok=True)
        streak = 0
        if streak_path.is_file():
            raw = json.loads(streak_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                streak = int(raw.get("consecutive_zero_fills", 0) or 0)
        if len(selected) > 0 and placed == 0:
            streak += 1
        elif placed > 0:
            streak = 0
        streak_path.write_text(
            json.dumps({"consecutive_zero_fills": streak}, indent=2),
            encoding="utf-8",
        )
        if streak >= 3:
            send_telegram(
                "⚠️ BLITZ FAILING — 0 fills in last 3 runs, needs attention"
            )
            streak_path.write_text(
                json.dumps({"consecutive_zero_fills": 0}, indent=2),
                encoding="utf-8",
            )
    except Exception as exc:
        logger.warning("Blitz zero-fill streak file failed: %s", exc)

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
