"""Kalshi rapid cycle — Gate A (BTC/ETH/S&P) or Gate B (broad), exits first, refill to N positions.

**Probability = cost to buy** (best ask): high ask → low profit per contract before fees.
Deployable cash uses Kalshi effective capital (``KALSHI_CASH_RESERVE_PCT``, default 20%).

State: ``shark/state/kalshi_positions.json``. Scheduler runs ``run_simple_scan`` on a fixed
30-second interval (see ``scheduler.build_shark_scheduler``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

_GATE_A_SERIES_DEFAULT: Tuple[str, ...] = ("KXBTCD", "KXETHD", "KXINX", "KXSPX", "KXSP500")


def _kalshi_crypto_series(ticker: str) -> bool:
    u = ticker.upper()
    return any(x in u for x in ("KXBTCD", "KXBTC", "KXETHD", "KXETH"))


def _kalshi_crypto_market_hours_ok(ticker: str) -> bool:
    """Day A: BTC/ETH Kalshi series only trade 9am–5pm ET Mon–Fri."""
    if not _kalshi_crypto_series(ticker):
        return True
    from datetime import datetime

    from zoneinfo import ZoneInfo

    et = datetime.now(ZoneInfo("America/New_York"))
    if et.weekday() >= 5:
        return False
    return 9 <= et.hour < 17


def _parse_kalshi_threshold_usd(ticker: str) -> Optional[float]:
    try:
        last = str(ticker).rsplit("-", 1)[-1].strip()
        if len(last) < 2 or last[0] not in "TtBb":
            return None
        raw = last[1:].replace(".99", "")
        v = float(raw)
        return v if v > 0 else None
    except Exception:
        return None


def _price_in_kalshi_range(ticker: str, current_price: float) -> bool:
    """
    For BTC/ETH Kalshi dailies: parsed threshold vs spot (± KALSHI_RANGE_MAX_DISTANCE_USD).
    If current_price <= 0, returns True (do not block on missing spot).
    """
    if current_price <= 0:
        return True
    if not _kalshi_crypto_series(ticker):
        return True
    thr = _parse_kalshi_threshold_usd(ticker)
    if thr is None:
        return True
    max_d = max(1.0, _parse_float("KALSHI_RANGE_MAX_DISTANCE_USD", 500.0))
    return abs(current_price - thr) <= max_d


def _fetch_btc_eth_spot() -> Tuple[Optional[float], Optional[float]]:
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        c = CoinbaseClient()
        pr = c.get_prices(["BTC-USD", "ETH-USD"])

        def mid(pid: str) -> Optional[float]:
            t = pr.get(pid)
            if not t:
                return None
            bid, ask = t
            if bid <= 0 and ask <= 0:
                return None
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            return max(bid, ask)

        return mid("BTC-USD"), mid("ETH-USD")
    except Exception:
        return None, None


def _spot_for_kalshi_ticker(
    ticker: str,
    btc: Optional[float],
    eth: Optional[float],
) -> Optional[float]:
    if not _kalshi_crypto_series(ticker):
        return None
    u = ticker.upper()
    if "ETH" in u:
        return eth
    return btc


def _simple_scan_gate() -> str:
    """``a`` = Gate A (BTC/ETH/S&P strict), ``b`` = Gate B (broad), ``legacy`` = env-only."""
    if _env_truthy("KALSHI_GATE_A_ENABLED", "false"):
        return "a"
    if _env_truthy("KALSHI_GATE_B_ENABLED", "false"):
        return "b"
    return "legacy"


def _gate_a_series_tickers() -> Tuple[str, ...]:
    raw = (os.environ.get("KALSHI_GATE_A_SERIES") or "").strip()
    if raw:
        return tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return _GATE_A_SERIES_DEFAULT


def _gate_min_prob(gate: str) -> float:
    if gate == "a":
        return max(0.5, min(0.99, _parse_float("KALSHI_SIMPLE_MIN_PROB", 0.90)))
    if gate == "b":
        return max(0.5, min(0.99, _parse_float("KALSHI_SIMPLE_MIN_PROB", 0.85)))
    return max(0.5, min(0.99, _parse_float("KALSHI_SIMPLE_MIN_PROB", 0.85)))


def _gate_max_ttr_sec(gate: str) -> float:
    if gate == "a":
        return max(60.0, _parse_float("KALSHI_SIMPLE_MAX_TTR", 7200.0))
    if gate == "b":
        return max(60.0, min(86400.0, _parse_float("KALSHI_SIMPLE_MAX_TTR", 3600.0)))
    lo = max(30.0, _parse_float("KALSHI_SIMPLE_TTR_MIN_SEC", 120.0))
    return max(lo, _parse_float("KALSHI_SIMPLE_TTR_MAX_SEC", 3600.0))


def _gate_max_positions(gate: str) -> int:
    if gate == "a":
        return max(1, _parse_int("KALSHI_SIMPLE_MAX_TRADES", 5))
    if gate == "b":
        return max(1, _parse_int("KALSHI_SIMPLE_MAX_TRADES", 10))
    return max(1, _parse_int("KALSHI_SIMPLE_MAX_TRADES", 10))


def _gate_per_order_cap_usd(deployable: float, gate: str) -> float:
    d = max(0.0, float(deployable))
    if gate == "a":
        return max(0.01, min(10.0, d * 0.10))
    if gate == "b":
        return max(0.01, min(5.0, d * 0.05))
    mx = max(1, _parse_int("KALSHI_SIMPLE_MAX_TRADES", 10))
    return max(0.01, min(10.0, d * 0.20 / float(mx)))


def _is_sp_index_ticker(ticker: str) -> bool:
    u = (ticker or "").upper()
    return "KXINX" in u or "KXSPX" in u or "KXSP500" in u or u.startswith("INX-")


def _fetch_spx_spot() -> Optional[float]:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1d&interval=5m"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; EzrasTradingAI/1.0)"}
    try:
        with httpx.Client(timeout=12.0) as h:
            r = h.get(url, headers=headers)
            r.raise_for_status()
            j = r.json()
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return None
        meta = res[0].get("meta") or {}
        px = meta.get("regularMarketPrice")
        if px is not None:
            return float(px)
        ind = res[0].get("indicators", {}).get("quote") or [{}]
        closes = (ind[0] or {}).get("close") or []
        for v in reversed(closes):
            if v is not None:
                return float(v)
    except Exception as exc:
        logger.debug("S&P spot fetch failed: %s", exc)
    return None


def _spx_within_threshold_pct(ticker: str, spot: Optional[float], pct: float) -> bool:
    if spot is None or spot <= 0:
        return False
    thr = _parse_kalshi_threshold_usd(ticker)
    if thr is None or thr <= 0:
        return False
    return abs(spot - thr) / max(spot, 1.0) <= pct


def _skip_long_dated_or_championship(ticker: str, close_ts: Optional[float]) -> Optional[str]:
    t = (ticker or "").upper()
    if close_ts and close_ts > 0:
        y = datetime.fromtimestamp(float(close_ts), tz=timezone.utc).year
        if y >= 2028 and any(x in t for x in ("KXNBA", "KXMLB", "NBA", "MLB")):
            return "championship_or_far_future_2028"
    if "CHAMP" in t and ("KXNBA" in t or "KXMLB" in t or "NBA" in t or "MLB" in t):
        return "championship_future"
    return None


def _interpret_probability(
    ticker: str,
    side: str,
    prob: float,
    yes_bid: float,
    no_bid: float,
) -> Dict[str, Any]:
    """
    Interpret what probability MEANS for this market.

    CRITICAL: High prob = LOW profit per contract
    (e.g. 95% → ~$0.05 profit per contract if correct).

    DANGER ZONES: range markets vs threshold; one-sided books.
    """
    edge = max(0.0, 1.0 - prob)
    profit_per_contract = edge

    market_type = "unknown"
    u = ticker.upper()
    if "KXBTCD" in u or "KXETHD" in u:
        market_type = "daily_range"
    elif "KXBTC" in u or "KXETH" in u:
        market_type = "threshold"
    elif "KXINX" in u or "KXSPX" in u or "KXSP500" in u:
        market_type = "index"
    elif any(s in u for s in ("NBA", "NFL", "MLB", "NHL")):
        market_type = "sports"

    warnings: List[str] = []

    if market_type == "daily_range":
        warnings.append(
            "RANGE MARKET: Verify current price is INSIDE this range before buying",
        )

    if prob >= 0.98:
        warnings.append(
            f"VERY HIGH PROB ({prob:.0%}): Only ${profit_per_contract:.3f} profit per contract. "
            f"Need many contracts for meaningful profit.",
        )

    if yes_bid == 0.0 and no_bid >= 0.95:
        warnings.append(
            "NO SIDE ONLY: YES has no buyers. Betting NO = betting condition FAILS. "
            "Verify this makes sense.",
        )

    denom = max(profit_per_contract, 1e-9)
    contracts_for_50c = max(1, int(0.50 / denom))

    return {
        "ticker": ticker,
        "side": side,
        "probability": prob,
        "win_condition": (
            "YES resolves TRUE" if str(side).lower() == "yes" else "NO resolves TRUE (YES FAILS)"
        ),
        "cost_per_contract": prob,
        "payout_per_contract": 1.0,
        "profit_per_contract": profit_per_contract,
        "contracts_for_50c_profit": contracts_for_50c,
        "market_type": market_type,
        "warnings": warnings,
        "is_high_confidence": prob >= 0.85,
        "expected_value": profit_per_contract,
    }


def _filter_simple_candidates(
    candidates: List[Dict[str, Any]],
    now: float,
    gate: str = "legacy",
) -> List[Dict[str, Any]]:
    """Calendar / spot checks after REST scan (hours, range, S&P vs threshold, Gate A/B skips)."""
    utc_now = datetime.now(timezone.utc)
    btc, eth = _fetch_btc_eth_spot()
    need_spx = any(_is_sp_index_ticker(str(x.get("ticker") or "")) for x in candidates)
    spx = _fetch_spx_spot() if need_spx else None
    sp_tol = max(0.001, min(0.05, _parse_float("KALSHI_SPX_THRESHOLD_PCT", 0.01)))

    out: List[Dict[str, Any]] = []
    for c in candidates:
        t = str(c.get("ticker") or "")
        close_ts = c.get("close_ts")
        close_dt: Optional[datetime] = None
        if close_ts is not None:
            try:
                close_dt = datetime.fromtimestamp(float(close_ts), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                close_dt = None

        skip_ch = _skip_long_dated_or_championship(t, float(close_ts) if close_ts else None)
        if skip_ch:
            logger.info("Skip %s: %s", t, skip_ch)
            continue

        if gate == "b" and close_dt is not None:
            days_to_close = (close_dt - utc_now).days
            if days_to_close > 7:
                logger.info("Skip %s: closes in %d days", t, days_to_close)
                continue

        if gate == "a" and close_dt is not None:
            if close_dt.year >= 2028:
                logger.info("Skip %s: close year %d (not same-day index)", t, close_dt.year)
                continue
            if close_dt.date() != utc_now.date():
                logger.info(
                    "Skip %s: Gate A requires same-day resolution (close=%s)",
                    t,
                    close_dt.date().isoformat(),
                )
                continue

        if not _kalshi_crypto_market_hours_ok(t):
            logger.debug("SIMPLE: skip %s (crypto market hours)", t)
            continue

        if _kalshi_crypto_series(t):
            spot = _spot_for_kalshi_ticker(t, btc, eth)
            if gate in ("a", "b") and (spot is None or spot <= 0):
                logger.info("Skip %s: no spot quote for crypto validation", t)
                continue
            if spot is not None and spot > 0 and not _price_in_kalshi_range(t, spot):
                thr = _parse_kalshi_threshold_usd(t)
                logger.info(
                    "Skip %s: spot vs threshold — spot=$%.0f threshold=%s dist=$%.0f",
                    t,
                    spot,
                    f"{thr:.0f}" if thr is not None else "?",
                    abs(spot - thr) if thr is not None else -1.0,
                )
                continue

        if _is_sp_index_ticker(t):
            if not _spx_within_threshold_pct(t, spx, sp_tol):
                thr = _parse_kalshi_threshold_usd(t)
                logger.info(
                    "Skip %s: S&P spot vs threshold — spot=%s threshold=%s (need within %.1f%%)",
                    t,
                    f"${spx:.2f}" if spx else "unavailable",
                    f"{thr:.0f}" if thr is not None else "?",
                    sp_tol * 100.0,
                )
                continue

        prob = float(c.get("prob") or 0.0)
        side = str(c.get("side") or "yes")
        yes_b = float(c.get("yes_bid") or 0.0)
        no_b = float(c.get("no_bid") or 0.0)
        profit_per_contract = max(0.0, 1.0 - prob)
        n_for_dollar = int(1 / max(0.01, profit_per_contract))

        interpretation = _interpret_probability(t, side, prob, yes_b, no_b)
        c["prob_interpretation"] = interpretation

        logger.info(
            "KALSHI TRADE: %s side=%s prob=%.0f%% profit_per_contract=$%.3f "
            "need %d contracts for $1 profit",
            t,
            side,
            prob * 100.0,
            profit_per_contract,
            n_for_dollar,
        )
        logger.info(
            "PROB CHECK: %s %s %.0f%% → profit/contract=$%.3f need %d contracts for $0.50 warnings=%s",
            t,
            side,
            prob * 100,
            interpretation["profit_per_contract"],
            interpretation["contracts_for_50c_profit"],
            interpretation["warnings"] or "none",
        )
        out.append(c)
    return out


def _parse_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_truthy(name: str, default: str = "false") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def _ai_truthy(name: str, default: str = "true") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def _state_path() -> Path:
    return shark_state_path("kalshi_positions.json")


def _default_state() -> Dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "positions": [],
        "daily_profit": 0.0,
        "daily_date": today,
        "trades_today": 0,
        "wins_today": 0,
        "losses_today": 0,
        "hour_cycle_count": 0,
        "hour_trade_count": 0,
        "hour_start_unix": time.time(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def load_simple_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            s = _default_state()
            s.update(raw)
            return s
    except Exception as exc:
        logger.warning("kalshi_positions.json load failed: %s", exc)
    return _default_state()


def save_simple_state(state: Dict[str, Any]) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("kalshi_positions.json save failed: %s", exc)


def _reset_daily_if_needed(state: Dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("daily_date") != today:
        state["daily_date"] = today
        state["daily_profit"] = 0.0
        state["trades_today"] = 0
        state["wins_today"] = 0
        state["losses_today"] = 0


def _series_tickers() -> Tuple[str, ...]:
    raw = (os.environ.get("KALSHI_SIMPLE_MARKETS") or "KXBTC,KXETH,KXINX").strip()
    return tuple(s.strip().upper() for s in raw.split(",") if s.strip())


def _available_deployable_usd() -> float:
    """Kalshi cash after reserve — same as ``balance * (1 - reserve)`` when API/env synced."""
    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.state_store import load_capital

    book = load_capital()
    return max(0.0, effective_capital_for_outlet("kalshi", float(book.current_capital)))


def _per_position_usd(available: float) -> float:
    """``available * KALSHI_SIMPLE_POSITION_PCT`` (fraction of deployable per slot)."""
    pct = max(0.001, min(0.5, _parse_float("KALSHI_SIMPLE_POSITION_PCT", 0.08)))
    return max(0.0, float(available) * pct)


def _profit_pct() -> float:
    return max(1e-6, min(1.0, _parse_float("KALSHI_SIMPLE_PROFIT_PCT", 0.10)))


def _stop_pct() -> float:
    return max(1e-6, min(1.0, _parse_float("KALSHI_SIMPLE_STOP_PCT", 0.05)))


def _time_stop_min() -> float:
    return max(0.5, _parse_float("KALSHI_SIMPLE_TIME_STOP_MIN", 5.0))


def _fetch_simple_candidates(
    client: Any,
    now: float,
) -> List[Dict[str, Any]]:
    from trading_ai.shark.outlets.kalshi import (
        _kalshi_yes_no_ask_from_market_row,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
        fetch_kalshi_orderbook_best_ask_cents,
    )

    gate = _simple_scan_gate()
    min_prob = _gate_min_prob(gate)
    min_net = max(0.0, _parse_float("KALSHI_SIMPLE_MIN_NET_EDGE", 0.015))
    ttr_lo = max(30.0, _parse_float("KALSHI_SIMPLE_TTR_MIN_SEC", 120.0))
    ttr_hi = _gate_max_ttr_sec(gate)
    if ttr_hi < ttr_lo:
        ttr_hi = ttr_lo
    api_limit = max(50, min(500, _parse_int("KALSHI_SIMPLE_SERIES_LIMIT", 200)))
    gate_b_cap = max(200, min(20000, _parse_int("KALSHI_GATE_B_MAX_MARKETS", 2000)))

    merged: Dict[str, Dict[str, Any]] = {}
    if gate == "b":
        try:
            rows = client.fetch_all_open_markets(max_rows=gate_b_cap)
            for m in rows:
                if isinstance(m, dict):
                    tid = str(m.get("ticker") or "").strip()
                    if tid:
                        merged[tid] = m
        except Exception as exc:
            logger.warning("simple scan Gate B open-markets fetch failed: %s", exc)
    else:
        series_src = _gate_a_series_tickers() if gate == "a" else _series_tickers()
        for ser in series_src:
            try:
                j = client._request(
                    "GET",
                    "/markets",
                    params={"status": "open", "limit": api_limit, "series_ticker": ser},
                )
                for m in j.get("markets") or []:
                    if isinstance(m, dict):
                        tid = str(m.get("ticker") or "").strip()
                        if tid:
                            merged[tid] = m
            except Exception as exc:
                logger.warning("simple scan fetch %s failed: %s", ser, exc)

    out: List[Dict[str, Any]] = []
    for m in merged.values():
        try:
            ticker = str(m.get("ticker") or "").strip()
            if not ticker:
                continue
            row = dict(m)
            y_bid, n_bid, _, _ = _kalshi_yes_no_from_market_row(row)
            if y_bid <= 0 or n_bid <= 0:
                try:
                    row = client.enrich_market_with_detail_and_orderbook(dict(m))
                    y_bid, n_bid, _, _ = _kalshi_yes_no_from_market_row(row)
                except Exception:
                    continue
            if y_bid <= 0 or n_bid <= 0:
                continue
            close_ts = _parse_close_timestamp_unix(row)
            if close_ts is None:
                continue
            ttr = close_ts - now
            if not (ttr_lo <= ttr <= ttr_hi):
                continue

            yes_ask, no_ask, _, _ = _kalshi_yes_no_ask_from_market_row(row)
            if yes_ask is None or no_ask is None:
                ya_c, na_c = fetch_kalshi_orderbook_best_ask_cents(ticker, client)
                if yes_ask is None and ya_c is not None:
                    yes_ask = ya_c / 100.0
                if no_ask is None and na_c is not None:
                    no_ask = na_c / 100.0
            if yes_ask is None or no_ask is None:
                continue

            side: Optional[str] = None
            px = 0.0
            prob = 0.0
            if yes_ask >= min_prob and (1.0 - yes_ask) >= min_net:
                side, px, prob = "yes", yes_ask, yes_ask
            elif no_ask >= min_prob and (1.0 - no_ask) >= min_net:
                side, px, prob = "no", no_ask, no_ask
            if side is None:
                continue

            profit_per_contract = 1.0 - px
            out.append(
                {
                    "ticker": ticker,
                    "ttr": ttr,
                    "prob": float(prob),
                    "side": side,
                    "price": float(px),
                    "yes_bid": float(y_bid),
                    "no_bid": float(n_bid),
                    "yes_ask": float(yes_ask),
                    "no_ask": float(no_ask),
                    "close_ts": float(close_ts),
                    "profit_per_contract": float(profit_per_contract),
                }
            )
        except Exception:
            continue

    out.sort(key=lambda x: (-x["prob"], x["ttr"]))
    return _filter_simple_candidates(out, now, gate)


def _position_pnl_usd(
    pos: Dict[str, Any],
    y: float,
    n: float,
) -> float:
    side = str(pos.get("side") or "yes").lower()
    entry = float(pos.get("entry_price") or 0.0)
    contracts = float(pos.get("contracts") or 0.0)
    if entry <= 0 or contracts <= 0:
        return 0.0
    cur = y if side == "yes" else n
    return contracts * (cur - entry)


def _check_exits_first(
    state: Dict[str, Any],
    client: Any,
) -> int:
    from trading_ai.shark.outlets.kalshi import KalshiClient, _kalshi_yes_no_from_market_row

    if not isinstance(client, KalshiClient):
        return 0

    available = _available_deployable_usd()
    position_pct = max(0.001, min(0.5, _parse_float("KALSHI_SIMPLE_POSITION_PCT", 0.08)))
    profit_pct = _profit_pct()
    stop_pct = _stop_pct()
    tmax = _time_stop_min() * 60.0
    now = time.time()

    remaining: List[Dict[str, Any]] = []
    exits = 0
    for pos in list(state.get("positions") or []):
        if bool(pos.get("exit_submitted")):
            remaining.append(pos)
            continue

        tid = str(pos.get("ticker") or "").strip()
        if not tid:
            remaining.append(pos)
            continue
        entry_t = float(pos.get("entry_time") or 0.0)
        age_min = (now - entry_t) / 60.0 if entry_t > 0 else 0.0
        contracts = float(pos.get("contracts") or 0.0)
        entry_prob = float(pos.get("entry_prob") or 0.0)

        try:
            mj = client.get_market(tid)
            inner = mj.get("market") if isinstance(mj.get("market"), dict) else mj
            if not isinstance(inner, dict):
                inner = {}
            y, n, _, _ = _kalshi_yes_no_from_market_row(inner)
        except Exception as exc:
            logger.debug("simple exit price fetch failed %s: %s", tid, exc)
            remaining.append(pos)
            continue

        pnl = _position_pnl_usd(pos, y, n)
        raw_tgt_usd = (os.environ.get("KALSHI_SIMPLE_TARGET_PROFIT") or "").strip()
        raw_sl_usd = (os.environ.get("KALSHI_SIMPLE_STOP_LOSS") or "").strip()
        if raw_tgt_usd and raw_sl_usd:
            profit_target_usd = max(0.01, float(raw_tgt_usd))
            stop_usd = max(0.01, float(raw_sl_usd))
        else:
            per_position = available * position_pct
            stop_usd = per_position * stop_pct
            edge = max(0.0, 1.0 - entry_prob)
            profit_target_usd = edge * contracts * profit_pct

        exit_reason = ""
        if pnl >= profit_target_usd:
            exit_reason = "profit"
        elif pnl <= -stop_usd:
            exit_reason = "stop"
        elif entry_t > 0 and (now - entry_t) >= tmax:
            exit_reason = "time"

        if not exit_reason:
            remaining.append(pos)
            continue

        side = str(pos.get("side") or "yes").lower()
        if side not in ("yes", "no"):
            side = "yes"
        cnt = max(1, int(contracts))

        pos_exit = dict(pos)
        pos_exit["exit_submitted"] = True
        try:
            res = client.place_order(ticker=tid, side=side, count=cnt, action="sell")
        except Exception as exc:
            logger.warning("RAPID EXIT sell failed %s: %s", tid, exc)
            pos_exit.pop("exit_submitted", None)
            remaining.append(pos_exit)
            continue

        fs = float(res.filled_size or 0.0)
        fp = float(res.filled_price or 0.0)
        realized = fs * fp - float(pos.get("cost") or 0.0) if fs > 0 else pnl
        if fs <= 0:
            realized = pnl
            pos_exit.pop("exit_submitted", None)
            remaining.append(pos_exit)
            continue

        logger.info(
            "RAPID EXIT: [%s] %s $%+.2f in %.1fmin (%s)",
            tid,
            exit_reason.upper(),
            realized,
            age_min,
            exit_reason,
        )

        state["daily_profit"] = float(state.get("daily_profit") or 0.0) + realized
        state["trades_today"] = int(state.get("trades_today") or 0) + 1
        state["hour_trade_count"] = int(state.get("hour_trade_count") or 0) + 1
        if realized >= 0:
            state["wins_today"] = int(state.get("wins_today") or 0) + 1
        else:
            state["losses_today"] = int(state.get("losses_today") or 0) + 1

        try:
            from trading_ai.shark.reporting import send_telegram

            open_n = len(remaining)
            daily = float(state.get("daily_profit") or 0.0)
            max_pos = max(1, _parse_int("KALSHI_SIMPLE_MAX_TRADES", 10))
            sym = tid.split("-")[0] if "-" in tid else tid
            send_telegram(
                f"💰 RAPID: {sym} ${realized:+.2f} in {age_min:.0f}min "
                f"[{open_n}/{max_pos} positions, PnL today ${daily:.2f}]"
            )
        except Exception:
            pass

        try:
            from trading_ai.shark.capital_effective import effective_capital_for_outlet
            from trading_ai.shark.state_store import load_capital
            from trading_ai.shark.supabase_logger import log_trade

            book = load_capital()
            balance = float(
                effective_capital_for_outlet("kalshi", float(book.current_capital))
            )
            exit_prob = float(fp) if fp > 0 else float(y if side == "yes" else n)
            hold_s = int(now - entry_t) if entry_t > 0 else 0
            sup_exit = "timeout" if exit_reason == "time" else exit_reason
            log_trade(
                platform="kalshi",
                gate="simple",
                product_id=tid,
                side="buy",
                strategy="simple_scan",
                entry_price=entry_prob,
                exit_price=exit_prob,
                size_usd=float(pos.get("cost") or 0.0),
                pnl_usd=realized,
                exit_reason=sup_exit,
                hold_seconds=hold_s,
                balance_after=balance,
            )
        except Exception:
            pass

        exits += 1

    state["positions"] = remaining
    return exits


def _maintain_positions(
    state: Dict[str, Any],
    client: Any,
    open_tickers: Set[str],
) -> int:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    if not isinstance(client, KalshiClient):
        return 0

    gate = _simple_scan_gate()
    max_n = _gate_max_positions(gate)
    now = time.time()
    placed = 0

    while len(state.get("positions") or []) < max_n:
        available = _available_deployable_usd()
        if gate in ("a", "b"):
            per_slot = _gate_per_order_cap_usd(available, gate)
            hard_cap = per_slot
        else:
            per_slot = _per_position_usd(available)
            hard_cap = min(10.0, available * 0.20 / float(max_n))
        raw_mx = (os.environ.get("KALSHI_SIMPLE_MAX_ORDER_USD") or "").strip()
        per_order_cap = min(hard_cap, float(raw_mx)) if raw_mx else hard_cap
        cands = _fetch_simple_candidates(client, now)
        ordered = cands
        if cands and _ai_truthy("KALSHI_AI_REVIEW_ENABLED", "true"):
            try:
                from trading_ai.shark.state_store import load_capital
                from trading_ai.shark.trade_advisor import get_combined_review

                book = load_capital()
                bal = float(book.current_capital)
                reviewed = get_combined_review(cands[:20], bal, "kalshi")
                if reviewed:
                    ordered = reviewed
                    logger.info(
                        "AI reviewed %d → %d ordered",
                        len(cands),
                        len(ordered),
                    )
            except Exception as e:
                logger.warning("AI review failed, using raw order: %s", e)
                ordered = cands
        picked: Optional[Dict[str, Any]] = None
        for c in ordered:
            t = str(c["ticker"])
            if t in open_tickers:
                continue
            picked = c
            break
        if picked is None:
            logger.info(
                "SIMPLE SCAN: need %s positions but no eligible markets",
                max_n - len(state.get("positions") or []),
            )
            break

        ticker = str(picked["ticker"])
        side = str(picked["side"])
        px = max(float(picked["price"]), 0.01)
        cnt = max(1, int(per_slot / px))
        est_cost = float(cnt * px)
        if est_cost > per_order_cap:
            cnt = max(1, int(per_order_cap / px))
            est_cost = float(cnt * px)

        from trading_ai.shark.mission import evaluate_trade_against_mission

        check = evaluate_trade_against_mission(
            platform="kalshi",
            product_id=ticker,
            size_usd=est_cost,
            probability=float(picked["prob"]),
            total_balance=available,
        )
        if not check["approved"]:
            logger.warning("MISSION BLOCK: %s", check["reason"])
            continue

        try:
            res = client.place_order(ticker=ticker, side=side, count=cnt, action="buy")
        except Exception as exc:
            logger.warning("simple scan buy failed %s: %s", ticker, exc)
            break

        fs = float(res.filled_size or 0.0)
        fp = float(res.filled_price or 0.0)
        if fs <= 0 or not (res.success is not False):
            open_tickers.add(ticker)
            continue

        cost = fs * fp
        pos = {
            "ticker": ticker,
            "side": side,
            "contracts": int(fs),
            "cost": cost,
            "entry_time": time.time(),
            "entry_prob": float(picked["prob"]),
            "entry_price": fp,
            "position_pct": _parse_float("KALSHI_SIMPLE_POSITION_PCT", 0.08),
            "exit_submitted": False,
        }
        state.setdefault("positions", []).append(pos)
        open_tickers.add(ticker)
        placed += 1

        logger.info(
            "SIMPLE BUY: %s %s x%s @ %.4f (notional ~%.4f of deployable)",
            ticker,
            side,
            int(fs),
            fp,
            cost,
        )
        save_simple_state(state)

    return placed


def _maybe_hourly_report(state: Dict[str, Any]) -> None:
    now = time.time()
    start = float(state.get("hour_start_unix") or 0.0)
    if start <= 0:
        state["hour_start_unix"] = now
        return
    if now - start < 3600.0:
        return

    try:
        from trading_ai.shark.capital_effective import effective_capital_for_outlet
        from trading_ai.shark.reporting import send_telegram
        from trading_ai.shark.state_store import load_capital

        book = load_capital()
        cap = effective_capital_for_outlet("kalshi", float(book.current_capital))
        cycles = int(state.get("hour_cycle_count") or 0)
        trades = int(state.get("hour_trade_count") or 0)
        w = int(state.get("wins_today") or 0)
        l = int(state.get("losses_today") or 0)
        t = w + l
        wr = (100.0 * w / t) if t > 0 else 0.0
        pnl = float(state.get("daily_profit") or 0.0)
        pct = (pnl / cap * 100.0) if cap > 0 else 0.0

        send_telegram(
            f"📊 HOUR REPORT (Kalshi simple):\n"
            f"Cycles: {cycles} | Trades: {trades}\n"
            f"Profit: ${pnl:+.2f} ({pct:+.1f}% vs deployable) | Win rate: {wr:.0f}%\n"
            f"Deployable ref: ${cap:.2f}"
        )
    except Exception as exc:
        logger.warning("hourly simple report failed: %s", exc)

    state["hour_cycle_count"] = 0
    state["hour_trade_count"] = 0
    state["hour_start_unix"] = now


def run_simple_scan() -> Dict[str, Any]:
    """One cycle: load state → exits → refill → save. Returns summary dict."""
    if not _env_truthy("KALSHI_SIMPLE_SCAN_ENABLED", "false"):
        return {"ok": False, "skipped": True, "reason": "disabled"}

    from trading_ai.shark.outlets.kalshi import KalshiClient

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        logger.info("Kalshi simple scan skipped — no credentials")
        return {"ok": False, "skipped": True, "reason": "no_credentials"}

    state = load_simple_state()
    _reset_daily_if_needed(state)
    now = time.time()
    if float(state.get("hour_start_unix") or 0.0) <= 0:
        state["hour_start_unix"] = now
    _maybe_hourly_report(state)

    state["hour_cycle_count"] = int(state.get("hour_cycle_count") or 0) + 1

    exits = _check_exits_first(state, client)

    open_tickers: Set[str] = {
        str(p.get("ticker") or "") for p in (state.get("positions") or []) if p.get("ticker")
    }
    placed = _maintain_positions(state, client, open_tickers)

    save_simple_state(state)

    return {
        "ok": True,
        "exits": exits,
        "placed": placed,
        "open": len(state.get("positions") or []),
    }
