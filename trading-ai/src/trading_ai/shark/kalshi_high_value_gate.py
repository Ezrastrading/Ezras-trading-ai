"""
Kalshi High Value Gate — obvious NO on index/crypto range markets with cheap contracts.

2–3 trades per day max (persisted). ~15% of deployable per design budget; per-trade cap
via ``KALSHI_HV_MAX_PER_TRADE``. Enable ``KALSHI_HV_GATE_ENABLED=true``.

Scans S&P / index / BTC / ETH series for markets with TTR 1–8h, very cheap YES (so NO is
~92%+) and spot far from the strike/range. Places NO buys with ``skip_pretrade_buy_gates``
so long-dated TTR passes Kalshi client gates.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)


def _truthy(name: str, default: str = "false") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def _pf(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def _pi(name: str, default: int) -> int:
    try:
        return int(float((os.environ.get(name) or "").strip() or default))
    except ValueError:
        return default


def _state_path() -> Path:
    return shark_state_path("kalshi_hv_gate_state.json")


def _default_state() -> Dict[str, Any]:
    return {
        "trades_date": "",
        "trades_today": 0,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _load_state() -> Dict[str, Any]:
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
        logger.warning("kalshi_hv_gate_state load: %s", exc)
    return _default_state()


def _save_state(state: Dict[str, Any]) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("kalshi_hv_gate_state save: %s", exc)


def _reset_daily_count(state: Dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("trades_date") != today:
        state["trades_date"] = today
        state["trades_today"] = 0


def can_trade_today(state: Dict[str, Any], max_per_day: int) -> bool:
    _reset_daily_count(state)
    return int(state.get("trades_today") or 0) < max(1, int(max_per_day))


def _available_deployable_usd() -> float:
    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.state_store import load_capital

    book = load_capital()
    return max(0.0, effective_capital_for_outlet("kalshi", float(book.current_capital)))


def _hv_series() -> Tuple[str, ...]:
    raw = (os.environ.get("KALSHI_HV_SERIES") or "").strip()
    if raw:
        return tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return ("KXINX", "KXSPX", "KXSP500", "KXBTCD", "KXETHD")


def _fetch_spx_spot() -> Optional[float]:
    try:
        from trading_ai.shark.kalshi_simple_scanner import _fetch_spx_spot as _spx

        return _spx()
    except Exception:
        return None


def _fetch_btc_eth() -> Tuple[Optional[float], Optional[float]]:
    try:
        from trading_ai.shark.kalshi_simple_scanner import _fetch_btc_eth_spot

        return _fetch_btc_eth_spot()
    except Exception:
        return None, None


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


def _parse_index_range_points(text: str) -> Optional[Tuple[float, float]]:
    """Parse a range like '6,975-6,999' or '6975-6999' from title/ticker."""
    if not text:
        return None
    s = re.sub(r",", "", text)
    m = re.search(r"(\d{3,5})\s*[-–]\s*(\d{3,5})", s)
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _is_index_ticker(ticker: str) -> bool:
    u = (ticker or "").upper()
    return "KXINX" in u or "KXSPX" in u or "KXSP500" in u or u.startswith("INX-")


def _is_crypto_ticker(ticker: str) -> bool:
    u = (ticker or "").upper()
    return any(x in u for x in ("KXBTCD", "KXBTC", "KXETHD", "KXETH"))


def _obvious_no_far_from_strike(
    ticker: str,
    inner: Dict[str, Any],
    spx: Optional[float],
    btc: Optional[float],
    eth: Optional[float],
) -> bool:
    """Return True if spot is clearly outside the market's range/threshold (NO is obvious)."""
    title = str(inner.get("title") or inner.get("subtitle") or "")
    combined = f"{ticker} {title}"
    min_index_gap = max(25.0, _pf("KALSHI_HV_MIN_INDEX_POINTS", 200.0))
    min_crypto = max(500.0, _pf("KALSHI_HV_MIN_CRYPTO_USD", 3000.0))
    thr_pct = max(0.005, min(0.5, _pf("KALSHI_HV_MIN_INDEX_PCT_AWAY", 0.02)))

    if _is_index_ticker(ticker):
        rng = _parse_index_range_points(combined)
        spot = spx
        if spot is None or spot <= 0:
            return False
        if rng:
            lo, hi = rng
            if lo <= spot <= hi:
                return False
            if spot < lo:
                return (lo - spot) >= min_index_gap
            return (spot - hi) >= min_index_gap
        thr = _parse_kalshi_threshold_usd(ticker)
        if thr is not None and thr > 0:
            return abs(spot - thr) / max(spot, 1.0) >= thr_pct
        return False

    if _is_crypto_ticker(ticker):
        u = ticker.upper()
        spot = eth if "ETH" in u else btc
        thr = _parse_kalshi_threshold_usd(ticker)
        if spot is None or spot <= 0 or thr is None:
            return False
        return abs(float(spot) - float(thr)) >= min_crypto

    return False


def find_high_value_trades(client: Any) -> List[Dict[str, Any]]:
    """
    Find index/crypto range markets where NO is cheap and spot is far from the condition.

    Budget: ``KALSHI_HV_ALLOC_PCT`` (default 15%) of Kalshi deployable (after reserve).
    """
    deploy = _available_deployable_usd()
    budget = deploy * _pf("KALSHI_HV_ALLOC_PCT", 0.15)
    max_day = max(1, _pi("KALSHI_HV_MAX_TRADES_DAY", 3))
    max_per_trade = min(_pf("KALSHI_HV_MAX_PER_TRADE", 5.0), budget / max(1, max_day))

    ttr_lo = max(60.0, _pf("KALSHI_HV_TTR_MIN_SEC", 3600.0))
    ttr_hi = max(ttr_lo, _pf("KALSHI_HV_TTR_MAX_SEC", 28800.0))
    min_no_prob = max(0.80, min(0.99, _pf("KALSHI_HV_MIN_NO_PROB", 0.92)))
    max_no_cost = max(0.02, min(0.5, _pf("KALSHI_HV_MAX_NO_ASK", 0.20)))
    min_expected = max(5.0, _pf("KALSHI_HV_MIN_EXPECTED_USD", 20.0))
    max_per_scan = max(1, _pi("KALSHI_HV_MAX_CANDIDATES_RETURN", 2))
    api_limit = max(50, min(300, _pi("KALSHI_HV_SERIES_LIMIT", 120)))

    spx = _fetch_spx_spot()
    btc, eth = _fetch_btc_eth()

    from trading_ai.shark.outlets.kalshi import (
        _kalshi_yes_no_ask_from_market_row,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
    )

    now = time.time()
    candidates: List[Dict[str, Any]] = []

    for ser in _hv_series():
        try:
            rows = client.fetch_markets_for_series(ser, limit=api_limit)
        except Exception as exc:
            logger.debug("HV fetch series %s: %s", ser, exc)
            continue
        for m in rows or []:
            if not isinstance(m, dict):
                continue
            ticker = str(m.get("ticker") or "").strip()
            if not ticker:
                continue
            try:
                inner = m.get("market") if isinstance(m.get("market"), dict) else dict(m)
                yb, nb, ya, na = _kalshi_yes_no_from_market_row(inner)
                if ya is None or na is None:
                    ya2, na2, _, _ = _kalshi_yes_no_ask_from_market_row(inner)
                    ya = ya if ya is not None else ya2
                    na = na if na is not None else na2
                if ya is None or na is None:
                    try:
                        inner = client.enrich_market_with_detail_and_orderbook(dict(m))
                        yb, nb, ya, na = _kalshi_yes_no_from_market_row(inner)
                        if ya is None or na is None:
                            ya2, na2, _, _ = _kalshi_yes_no_ask_from_market_row(inner)
                            ya = ya if ya is not None else ya2
                            na = na if na is not None else na2
                    except Exception:
                        continue
                if ya is None or na is None:
                    continue
                yes_ask = float(ya)
                no_ask = float(na)
            except Exception:
                continue

            close_ts = _parse_close_timestamp_unix(inner)
            if close_ts is None:
                continue
            ttr = float(close_ts) - now
            if not (ttr_lo <= ttr <= ttr_hi):
                continue

            no_prob = 1.0 - yes_ask
            if no_prob < min_no_prob:
                continue

            no_cost = no_ask
            if no_cost <= 0 or no_cost >= max_no_cost:
                continue

            if not _obvious_no_far_from_strike(ticker, inner, spx, btc, eth):
                continue

            contracts = int(max_per_trade / max(no_cost, 1e-9))
            if contracts < _pi("KALSHI_HV_MIN_CONTRACTS", 5):
                continue

            total_cost = contracts * no_cost
            max_payout = float(contracts) * 1.0
            expected_payout = max_payout * no_prob
            if expected_payout < min_expected:
                continue

            candidates.append(
                {
                    **m,
                    "hv_side": "no",
                    "hv_contracts": contracts,
                    "hv_cost": total_cost,
                    "hv_max_payout": max_payout,
                    "hv_expected": expected_payout,
                    "hv_no_prob": no_prob,
                    "hv_no_cost": no_cost,
                    "hv_ttr": ttr,
                }
            )
            logger.info(
                "HV CANDIDATE: %s NO≈%.0f%% contracts=%d cost=$%.2f exp_payout≈$%.2f",
                ticker,
                no_prob * 100.0,
                contracts,
                total_cost,
                expected_payout,
            )

    candidates.sort(key=lambda x: -float(x.get("hv_expected") or 0.0))
    return candidates[:max_per_scan]


def place_high_value_trade(client: Any, market: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Place one NO market buy; bump daily counter on success."""
    ticker = str(market.get("ticker") or "").strip()
    contracts = max(1, int(market.get("hv_contracts") or 0))
    cost = float(market.get("hv_cost") or 0.0)
    payout = float(market.get("hv_max_payout") or 0.0)
    prob = float(market.get("hv_no_prob") or 0.0)

    logger.info(
        "HV TRADE: %s NO × %d ~$%.2f cost → $%.2f max payout",
        ticker,
        contracts,
        cost,
        payout,
    )

    try:
        result = client.place_order(
            ticker=ticker,
            side="no",
            count=contracts,
            action="buy",
            order_type="market",
            skip_pretrade_buy_gates=True,
            min_order_prob=0.50,
        )
    except Exception as exc:
        logger.warning("HV trade failed %s: %s", ticker, exc)
        return False

    ok = bool(result.success) and bool(result.order_id)
    fs = float(result.filled_size or 0.0)
    if not ok or fs <= 0:
        logger.warning("HV trade not filled %s status=%s", ticker, getattr(result, "status", ""))
        return False

    state["trades_today"] = int(state.get("trades_today") or 0) + 1
    _save_state(state)

    try:
        from trading_ai.shark.reporting import send_telegram

        send_telegram(
            f"🎯 HV TRADE PLACED:\n"
            f"Market: {ticker}\n"
            f"Side: NO (~{prob*100:.0f}%)\n"
            f"Contracts: {contracts}\n"
            f"Cost: ~${cost:.2f}\n"
            f"Max payout: ~${payout:.2f}\n"
            f"Trades today: {state['trades_today']}"
        )
    except Exception:
        pass

    logger.info("HV TRADE SUCCESS: %s trades_today=%s", ticker, state["trades_today"])
    return True


def run_hv_scan(client: Any, balance: Optional[float] = None) -> int:
    """Fetch candidates and place up to max_per_scan trades. Returns count placed."""
    _ = balance
    if not _truthy("KALSHI_HV_GATE_ENABLED", "false"):
        return 0

    if not getattr(client, "has_kalshi_credentials", lambda: False)():
        logger.warning("HV Gate: no Kalshi credentials")
        return 0

    max_day = max(1, _pi("KALSHI_HV_MAX_TRADES_DAY", 3))
    state = _load_state()
    if not can_trade_today(state, max_day):
        return 0

    candidates = find_high_value_trades(client)
    if not candidates:
        return 0

    placed = 0
    max_orders = max(1, _pi("KALSHI_HV_MAX_ORDERS_PER_SCAN", 2))
    for c in candidates[:max_orders]:
        state = _load_state()
        if not can_trade_today(state, max_day):
            break
        if place_high_value_trade(client, c, state):
            placed += 1
            time.sleep(2.0)
    return placed
