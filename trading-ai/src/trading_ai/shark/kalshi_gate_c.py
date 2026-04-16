"""Kalshi Gate C — sports momentum with **percentage-based** sizing and exits.

State: ``shark/state/kalshi_gate_c_state.json``. Enable ``KALSHI_GATE_C_ENABLED=true``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

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
    return shark_state_path("kalshi_gate_c_state.json")


def _default_state() -> Dict[str, Any]:
    return {
        "positions": [],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _load() -> Dict[str, Any]:
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
        logger.warning("kalshi_gate_c_state load: %s", exc)
    return _default_state()


def _save(state: Dict[str, Any]) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("kalshi_gate_c_state save: %s", exc)


def _available_deployable_usd() -> float:
    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.state_store import load_capital

    book = load_capital()
    return max(0.0, effective_capital_for_outlet("kalshi", float(book.current_capital)))


def _per_position_usd(available: float, position_pct: float) -> float:
    pct = max(0.001, min(0.5, position_pct))
    return max(0.0, float(available) * pct)


def _sports_series() -> Tuple[str, ...]:
    raw = (os.environ.get("KALSHI_GATE_C_SERIES") or "").strip()
    if raw:
        return tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return (
        "KXMLBGAME",
        "KXNBAGAME",
        "KXNHLGAME",
        "KXSOCGAME",
        "KXNBATODAY",
        "KXNBA",
        "KXNFL",
    )


def run_gate_c() -> Dict[str, Any]:
    """One Gate C cycle: exits for open sports positions, then new buys. Returns summary dict."""
    if not _truthy("KALSHI_GATE_C_ENABLED", "false"):
        return {"ok": False, "reason": "disabled", "exits": 0, "placed": 0, "open": 0}

    from trading_ai.shark.kalshi_crypto import kalshi_ticker_is_crypto
    from trading_ai.shark.outlets.kalshi import (
        KalshiClient,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
    )
    from trading_ai.shark.reporting import send_telegram

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        return {"ok": False, "reason": "no_credentials", "exits": 0, "placed": 0, "open": 0}

    min_prob = max(0.5, min(0.99, _pf("KALSHI_SPORTS_MIN_PROB", 0.85)))
    ttr_lo = _pf("KALSHI_GATE_C_TTR_MIN_SEC", 300.0)
    ttr_hi = _pf("KALSHI_GATE_C_TTR_MAX_SEC", 3600.0)
    time_stop_sec = _pf("KALSHI_GATE_C_TIME_STOP_SEC", 120.0)
    max_pos = max(1, _pi("KALSHI_GATE_C_POSITIONS", 5))
    pos_pct = max(0.001, min(0.5, _pf("KALSHI_GATE_C_POSITION_PCT", 0.08)))
    profit_pct = max(1e-6, min(1.0, _pf("KALSHI_GATE_C_PROFIT_PCT", 0.10)))
    stop_pct = max(1e-6, min(1.0, _pf("KALSHI_GATE_C_STOP_PCT", 0.05)))
    api_limit = max(50, min(500, _pi("KALSHI_GATE_C_SERIES_LIMIT", 200)))

    state = _load()
    now = time.time()
    positions: List[Dict[str, Any]] = list(state.get("positions") or [])
    exits = 0

    remaining: List[Dict[str, Any]] = []
    for pos in positions:
        if bool(pos.get("exit_submitted")):
            remaining.append(pos)
            continue

        tid = str(pos.get("ticker") or "").strip()
        side = str(pos.get("side") or "yes").lower()
        if side not in ("yes", "no"):
            side = "yes"
        entry_prob = float(pos.get("entry_prob") or 0.0)
        contracts = float(pos.get("contracts") or 0.0)
        try:
            mj = client.get_market(tid)
            inner = mj.get("market") if isinstance(mj.get("market"), dict) else mj
            if not isinstance(inner, dict):
                inner = {}
            y, n, _, _ = _kalshi_yes_no_from_market_row(inner)
            close_ts = _parse_close_timestamp_unix(inner)
        except Exception as exc:
            logger.debug("gate C price %s: %s", tid, exc)
            remaining.append(pos)
            continue

        ttr = (close_ts - now) if close_ts else 0.0
        cur = y if side == "yes" else n
        pnl = contracts * (cur - float(pos.get("entry_price") or 0.0))

        av = _available_deployable_usd()
        per_pos = _per_position_usd(av, pos_pct)
        stop_usd = per_pos * stop_pct
        edge = max(0.0, 1.0 - entry_prob)
        profit_target_usd = edge * contracts * profit_pct

        exit_reason = ""
        if pnl >= profit_target_usd:
            exit_reason = "profit"
        elif pnl <= -stop_usd:
            exit_reason = "stop"
        elif 0 < ttr < time_stop_sec:
            exit_reason = "time_ttr"

        if not exit_reason:
            remaining.append(pos)
            continue

        cnt = max(1, int(contracts))
        pos_exit = dict(pos)
        pos_exit["exit_submitted"] = True
        try:
            res = client.place_order(ticker=tid, side=side, count=cnt, action="sell")
        except Exception as exc:
            logger.warning("Gate C sell failed %s: %s", tid, exc)
            pos_exit.pop("exit_submitted", None)
            remaining.append(pos_exit)
            continue

        if res.success is False or float(res.filled_size or 0) <= 0:
            pos_exit.pop("exit_submitted", None)
            remaining.append(pos_exit)
            continue

        exits += 1
        logger.info("GATE C EXIT: %s %s prob=%.4f", tid, exit_reason, cur)
        try:
            send_telegram(
                f"🏀 Gate C EXIT: {tid[:28]}… {exit_reason} pnl≈${pnl:.2f} prob={cur:.2f}"
            )
        except Exception:
            pass

    state["positions"] = remaining
    _save(state)

    open_ids: Set[str] = {str(p.get("ticker") or "") for p in state["positions"] if p.get("ticker")}
    placed = 0
    merged: Dict[str, Dict[str, Any]] = {}
    for ser in _sports_series():
        try:
            j = client._request(
                "GET",
                "/markets",
                params={"status": "open", "limit": api_limit, "series_ticker": ser},
            )
            for m in j.get("markets") or []:
                if isinstance(m, dict):
                    t = str(m.get("ticker") or "").strip()
                    if t:
                        merged[t] = m
        except Exception as exc:
            logger.warning("gate C fetch %s: %s", ser, exc)

    targets: List[Dict[str, Any]] = []
    for m in merged.values():
        try:
            ticker = str(m.get("ticker") or "").strip()
            if not ticker or kalshi_ticker_is_crypto(ticker):
                continue
            row = dict(m)
            y, n, _, _ = _kalshi_yes_no_from_market_row(row)
            if y <= 0 or n <= 0:
                continue
            close_ts = _parse_close_timestamp_unix(row)
            if close_ts is None:
                continue
            ttr = close_ts - now
            if not (ttr_lo <= ttr <= ttr_hi):
                continue
            prob = max(y, n)
            if prob < min_prob:
                continue
            side = "yes" if y >= n else "no"
            targets.append(
                {
                    "ticker": ticker,
                    "side": side,
                    "prob": prob,
                    "y": y,
                    "n": n,
                    "ttr": ttr,
                }
            )
        except Exception:
            continue

    targets.sort(key=lambda x: (-x["prob"], x["ttr"]))
    state = _load()
    av2 = _available_deployable_usd()
    per_buy = _per_position_usd(av2, pos_pct)

    for t in targets:
        if len(state.get("positions") or []) >= max_pos:
            break
        tid = t["ticker"]
        if tid in open_ids:
            continue
        side = t["side"]
        px = max(float(t["y"] if side == "yes" else t["n"]), 0.01)
        cnt = max(1, int(per_buy / px))
        try:
            res = client.place_order(ticker=tid, side=side, count=cnt, action="buy")
        except Exception as exc:
            logger.warning("Gate C buy failed %s: %s", tid, exc)
            break
        fs = float(res.filled_size or 0.0)
        if fs <= 0 or res.success is False:
            open_ids.add(tid)
            continue
        fp = float(res.filled_price or px)
        state.setdefault("positions", []).append(
            {
                "ticker": tid,
                "side": side,
                "contracts": int(fs),
                "entry_yes": float(t["y"]),
                "entry_no": float(t["n"]),
                "entry_prob": float(t["prob"]),
                "entry_price": fp,
                "entry_time": time.time(),
                "position_pct": pos_pct,
                "exit_submitted": False,
            }
        )
        open_ids.add(tid)
        placed += 1
        logger.info("GATE C BUY: %s %s x%s @ %.4f", tid, side, int(fs), fp)
        _save(state)

    open_n = len(state.get("positions") or [])
    return {"ok": True, "exits": exits, "placed": placed, "open": open_n}


def run_kalshi_gate_c_scan() -> int:
    """Backward-compatible: total exit+buy actions (0 if disabled)."""
    r = run_gate_c()
    if not isinstance(r, dict) or not r.get("ok"):
        return 0
    return int(r.get("exits", 0)) + int(r.get("placed", 0))
