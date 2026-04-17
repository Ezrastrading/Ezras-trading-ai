"""
Coinbase — **four engines** (E1–E4) when ``COINBASE_GATES_MODE=false``, or **Gates A–C**
when ``COINBASE_GATES_MODE=true`` (mission-aligned $1M path):

**Gate A — Night gainer** (10pm–8am ET): broad USD universe, hour momentum + volume; trail / TP / SL / max hold.
**Gate B — Day momentum** (9am–10pm ET): liquid alts list; RSI / EMA / MACD confirmation.
**Gate C — BTC/ETH breakout** (24/7): resistance breakout + volume spike.

Deployable = ``balance * COINBASE_MAX_DEPLOY_PCT`` (default 0.80; ``COINBASE_RESERVE_PCT`` cash reserve).

Exits: ``coinbase_exit_check`` every **10s** (trail stop, profit target, stop loss, max hold, dawn sweep).
``coinbase_scan`` every **5m**: exits first, then Gate C → A → B buys. Engine mode unchanged.

State: ``shark/state/coinbase_positions.json`` (+ trade JSONL).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from decimal import ROUND_DOWN, Decimal
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_data_dir, shark_state_path
from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import OrderResult
from trading_ai.shark.outlets.coinbase import CoinbaseAuthError, CoinbaseClient
from trading_ai.shark.supabase_logger import log_trade

load_shark_dotenv()
logger = logging.getLogger(__name__)


def _mission_allows_coinbase_buy(product_id: str, order_usd: float, usd_balance: float) -> bool:
    from trading_ai.shark.mission import evaluate_trade_against_mission

    check = evaluate_trade_against_mission(
        platform="coinbase",
        product_id=product_id,
        size_usd=float(order_usd),
        probability=0.99,
        total_balance=float(usd_balance or 0.0),
    )
    if not check["approved"]:
        logger.warning("MISSION BLOCK: %s", check["reason"])
        return False
    return True


_E3_LIQUID = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD")
_E4_LIQUID = ("BTC-USD", "ETH-USD")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return default


# ─── GATE A: NIGHT GAINER ─────────────────
GATE_A_HOURS = list(range(22, 24)) + list(range(0, 8))  # 10pm–8am ET
GATE_A_MIN_HOUR_GAIN = _env_float("COINBASE_GATE_A_MIN_HOUR_GAIN", 0.055)
GATE_A_MIN_VOL_RATIO = _env_float("COINBASE_GATE_A_MIN_VOL_RATIO", 3.3)
GATE_A_TRAIL_STOP = _env_float("COINBASE_GATE_A_TRAIL_STOP", 0.018)
GATE_A_PROFIT_TARGET = _env_float("COINBASE_GATE_A_PROFIT_TARGET", 0.11)
GATE_A_STOP_LOSS = _env_float("COINBASE_GATE_A_STOP_LOSS", 0.027)
GATE_A_MAX_HOLD_MIN = _env_float("COINBASE_GATE_A_MAX_HOLD_MIN", 240.0)
GATE_A_ALLOCATION = _env_float("COINBASE_GATE_A_ALLOCATION", 0.11)
GATE_A_MAX_POSITIONS = int(_env_float("COINBASE_GATE_A_MAX_POSITIONS", 5.0))

# ─── GATE B: DAY MOMENTUM ─────────────────
GATE_B_HOURS = list(range(9, 22))  # 9am–10pm ET
GATE_B_RSI_CROSS = _env_float("COINBASE_GATE_B_RSI_CROSS", 50.0)
GATE_B_TRAIL_STOP = _env_float("COINBASE_GATE_B_TRAIL_STOP", 0.013)
GATE_B_PROFIT_TARGET = _env_float("COINBASE_GATE_B_PROFIT_TARGET", 0.033)
GATE_B_STOP_LOSS = _env_float("COINBASE_GATE_B_STOP_LOSS", 0.013)
GATE_B_MAX_HOLD_MIN = _env_float("COINBASE_GATE_B_MAX_HOLD_MIN", 132.0)
GATE_B_MIN_HOLD_MIN = _env_float("COINBASE_GATE_B_MIN_HOLD_MIN", 15.0)
GATE_B_ALLOCATION = _env_float("COINBASE_GATE_B_ALLOCATION", 0.088)
GATE_B_MAX_POSITIONS = int(_env_float("COINBASE_GATE_B_MAX_POSITIONS", 6.0))
GATE_B_PRODUCTS: Tuple[str, ...] = (
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "XRP-USD",
    "DOGE-USD",
    "ADA-USD",
    "AVAX-USD",
    "LINK-USD",
    "DOT-USD",
    "MATIC-USD",
    "UNI-USD",
)

# ─── GATE C: BTC/ETH BREAKOUT ─────────────
GATE_C_PRODUCTS: Tuple[str, ...] = ("BTC-USD", "ETH-USD")
GATE_C_TRAIL_STOP = _env_float("COINBASE_GATE_C_TRAIL_STOP", 0.011)
GATE_C_PROFIT_TARGET = _env_float("COINBASE_GATE_C_PROFIT_TARGET", 0.022)
GATE_C_STOP_LOSS = _env_float("COINBASE_GATE_C_STOP_LOSS", 0.011)
GATE_C_MAX_HOLD_MIN = _env_float("COINBASE_GATE_C_MAX_HOLD_MIN", 120.0)
GATE_C_MIN_HOLD_MIN = _env_float("COINBASE_GATE_C_MIN_HOLD_MIN", 10.0)
GATE_C_ALLOCATION = _env_float("COINBASE_GATE_C_ALLOCATION", 0.165)
GATE_C_MAX_POSITIONS = int(_env_float("COINBASE_GATE_C_MAX_POSITIONS", 2.0))
GATE_C_VOL_SPIKE = _env_float("COINBASE_GATE_C_VOL_SPIKE", 2.2)


def _min_order_usd(usd_balance: float) -> float:
    raw = (os.environ.get("COINBASE_MIN_ORDER_USD") or "").strip()
    if raw:
        try:
            return max(1e-12, float(raw))
        except (TypeError, ValueError):
            pass
    pct = _env_float("COINBASE_MIN_ORDER_PCT", 0.001)
    return max(1e-9, float(usd_balance) * pct)


def _gate_dynamic_order_usd(usd_balance: float, gate_max_positions: int) -> float:
    """
    Per-slot USD for Gates A/B: ``balance * COINBASE_MAX_DEPLOY_PCT * slice / max(max_positions, 10)``.

    Example: $55 × 0.80 × 0.50 / 10 ≈ $2.20 when ``gate_max_positions`` ≤ 10.
    ``COINBASE_GATE_AB_SLICE_PCT`` defaults to ``0.50`` (half of deploy bucket).
    """
    bal = max(0.0, float(usd_balance))
    deploy_pct = _env_float("COINBASE_MAX_DEPLOY_PCT", 0.80)
    slice_half = max(0.01, min(0.5, _env_float("COINBASE_GATE_AB_SLICE_PCT", 0.50)))
    denom = max(10, int(gate_max_positions))
    raw = bal * deploy_pct * slice_half / float(denom)
    return max(_min_order_usd(usd_balance), raw)


def _max_total_exposure_usd(usd_balance: float) -> float:
    raw = (os.environ.get("COINBASE_MAX_TOTAL_USD") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    return max(0.0, float(usd_balance) * _env_float("COINBASE_MAX_TOTAL_EXPOSURE_PCT", 20.0))


def _max_daily_loss_usd(usd_balance: float) -> float:
    raw = (os.environ.get("COINBASE_MAX_DAILY_LOSS") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    return max(0.0, float(usd_balance) * _env_float("COINBASE_MAX_DAILY_LOSS_PCT", 2.0))


def coinbase_enabled() -> bool:
    return _env_bool("COINBASE_ENABLED", False)


def _coinbase_gates_mode() -> bool:
    """Percent-based Gate A + Gate B (5+5) instead of engines E1–E4 buys."""
    return _env_bool("COINBASE_GATES_MODE", False)


def _reserve_pct() -> float:
    return _env_float("COINBASE_RESERVE_PCT", 0.20)


def _positions_path() -> Path:
    return shark_state_path("coinbase_positions.json")


def _trade_log_path() -> Path:
    p = shark_data_dir() / "logs" / "coinbase_trade_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _default_state() -> Dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "positions": [],
        "daily_pnl_usd": 0.0,
        "daily_pnl_date": today,
        "total_realized_usd": 0.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "hf_product_cache": [],
        "hf_product_cache_ts": 0.0,
        "e2_scan_ts": 0.0,
        "gh_vol_track": {},
        "hunter_cooldown": {},
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def load_coinbase_state() -> Dict[str, Any]:
    p = _positions_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state = _default_state()
            state.update(raw)
            return state
    except Exception as exc:
        logger.warning("coinbase_positions.json load error: %s — using defaults", exc)
    return _default_state()


def save_coinbase_state(state: Dict[str, Any]) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    p = _positions_path()
    try:
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("coinbase_positions.json save error: %s", exc)


def _log_trade(record: Dict[str, Any]) -> None:
    try:
        _trade_log_path().open("a", encoding="utf-8").write(
            json.dumps(record, default=str) + "\n"
        )
    except Exception as exc:
        logger.warning("Coinbase trade log write error: %s", exc)


def _cb_supabase_exit_reason(sell_reason: str, *, profit_scan: bool = False) -> str:
    """Map internal sell reason to Supabase taxonomy: profit | stop | timeout."""
    if profit_scan:
        return "profit"
    sl = sell_reason.lower()
    if "time" in sl or ">=" in sell_reason:
        return "timeout"
    if "loss_scan" in sl:
        return "stop"
    if "stop" in sl or "trail" in sl or "no_price" in sl:
        return "stop"
    if "tp" in sl or sl.startswith("tp"):
        return "profit"
    if "pending" in sl:
        return "stop"
    return "profit"


class _RateLimiter:
    def __init__(self, max_per_minute: Optional[int] = None) -> None:
        if max_per_minute is None:
            try:
                max_per_minute = max(
                    1,
                    int(float(os.environ.get("COINBASE_MAX_ORDERS_PER_MIN") or "180")),
                )
            except (TypeError, ValueError):
                max_per_minute = 180
        self._max = max_per_minute
        self._calls: deque[float] = deque()

    def allow(self) -> bool:
        now = time.time()
        while self._calls and now - self._calls[0] > 60.0:
            self._calls.popleft()
        if len(self._calls) >= self._max:
            return False
        self._calls.append(now)
        return True


def _reset_daily_pnl_if_needed(state: Dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl_usd"] = 0.0
        state["daily_pnl_date"] = today


def _cb_sym(product_id: str) -> str:
    return str(product_id or "").replace("-USD", "")


def _mid_at_or_before(
    hist: List[Tuple[float, float]], ts: float
) -> Optional[float]:
    old = [(t, p) for t, p in hist if t <= ts]
    if not old:
        return None
    return float(old[-1][1])


def _ema_series(closes: List[float], period: int) -> List[float]:
    """Exponential moving average; first value is SMA(seed) over first ``period`` bars."""
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / float(period)
    out = [ema]
    for x in closes[period:]:
        ema = x * k + ema * (1.0 - k)
        out.append(ema)
    return out


def _ema_last(closes: List[float], period: int) -> float:
    s = _ema_series(closes, period)
    return float(s[-1]) if s else 0.0


def _rsi_last(closes: List[float], period: int = 14) -> float:
    """Simple RSI from the last ``period`` one-bar changes (bounded 0–100)."""
    if len(closes) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g = gains / float(period)
    avg_l = losses / float(period)
    if avg_l <= 1e-12:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def _macd_line_last(closes: List[float]) -> Tuple[float, float]:
    """(macd_now, macd_prev) using EMA12−EMA26 on ``closes`` and ``closes[:-1]``."""
    if len(closes) < 28:
        return 0.0, 0.0
    m_now = _ema_last(closes, 12) - _ema_last(closes, 26)
    m_prev = (
        _ema_last(closes[:-1], 12) - _ema_last(closes[:-1], 26) if len(closes) > 28 else m_now
    )
    return m_now, m_prev


def _min_product_price_usd() -> float:
    """Minimum spot mid/bid for buys and Gate B / E2 candidate screens."""
    return max(1e-12, _env_float("COINBASE_MIN_PRODUCT_PRICE", 0.01))


def _min_volume_usd_24h() -> float:
    """Minimum 24h quote volume (USD) for buys and Gate B / E2 gainer screen."""
    return max(0.0, _env_float("COINBASE_MIN_VOLUME_USD", 500_000.0))


def _max_buy_spread_ratio() -> float:
    """Max (ask−bid)/bid for market buys (default 0.5%)."""
    return max(1e-9, _env_float("COINBASE_MAX_BUY_SPREAD_PCT", 0.005))


def _quote_volume_24h_usd(row: Dict[str, Any]) -> float:
    for k in (
        "approximate_quote_24h_volume",
        "quote_volume_24h",
        "volume_24h",
    ):
        v = row.get(k)
        if v is None or str(v).strip() == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return 0.0


def _total_open_cost(state: Dict[str, Any]) -> float:
    return sum(float(p.get("cost_usd") or 0.0) for p in state.get("positions") or [])


def _product_open_cost(state: Dict[str, Any], product_id: str) -> float:
    return sum(
        float(p.get("cost_usd") or 0.0)
        for p in (state.get("positions") or [])
        if p.get("product_id") == product_id
    )


def _has_open_product(state: Dict[str, Any], product_id: str) -> bool:
    return any(
        str(p.get("product_id")) == product_id for p in (state.get("positions") or [])
    )


def _max_per_coin_usd(usd_balance: float) -> float:
    raw = (os.environ.get("COINBASE_MAX_PER_COIN_USD") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    return max(0.0, float(usd_balance) * _env_float("COINBASE_MAX_PER_COIN_PCT", 0.06))


def _deployable_usd(usd_balance: float) -> float:
    return max(0.0, float(usd_balance) * _env_float("COINBASE_MAX_DEPLOY_PCT", 0.80))


def _engine_quarter_cap(usd_balance: float) -> float:
    return _deployable_usd(usd_balance) * 0.25


def _count_gate(state: Dict[str, Any], gate: str) -> int:
    g = (gate or "").strip().upper()
    return sum(
        1
        for p in (state.get("positions") or [])
        if str(p.get("gate") or "").strip().upper() == g
    )


def _is_abc_gate(gate: str) -> bool:
    return (gate or "").strip().upper() in ("A", "B", "C")


def _has_open_gate_product(state: Dict[str, Any], gate: str, product_id: str) -> bool:
    g = (gate or "").strip().upper()
    pid = str(product_id or "")
    return any(
        str(p.get("product_id")) == pid
        and str(p.get("gate") or "").strip().upper() == g
        for p in (state.get("positions") or [])
    )


def _can_buy_abc_gate(
    state: Dict[str, Any],
    gate: str,
    product_id: str,
    order_usd: float,
    usd_balance: float,
    max_gate_positions: int,
) -> Tuple[bool, str]:
    ok, reason = _can_buy_global(state, product_id, order_usd, usd_balance)
    if not ok:
        return False, reason
    if _has_open_gate_product(state, gate, product_id):
        return False, "already open in this gate"
    if _count_gate(state, gate) >= max_gate_positions:
        return False, f"gate {gate} max positions ({max_gate_positions})"
    return True, "ok"


def _gate_order_usd(usd_balance: float, position_pct: float) -> float:
    avail = max(0.0, float(usd_balance) * (1.0 - _reserve_pct()))
    raw = avail * max(0.001, min(0.5, float(position_pct)))
    return max(_min_order_usd(usd_balance), raw)


def _can_buy_gate(
    state: Dict[str, Any],
    gate: str,
    product_id: str,
    order_usd: float,
    usd_balance: float,
    max_gate_positions: int,
) -> Tuple[bool, str]:
    ok, reason = _can_buy_global(state, product_id, order_usd, usd_balance)
    if not ok:
        return False, reason
    if _has_open_product(state, product_id):
        return False, "already open on product"
    if _count_gate(state, gate) >= max_gate_positions:
        return False, f"gate {gate} max positions ({max_gate_positions})"
    return True, "ok"


def _infer_engine_from_legacy(pos: Dict[str, Any]) -> int:
    if pos.get("engine") is not None:
        try:
            return int(pos["engine"])
        except (TypeError, ValueError):
            pass
    if pos.get("hunter") or str(pos.get("strategy") or "") == "D":
        return 2
    st = str(pos.get("hf_engine") or pos.get("strategy") or "")
    pid = str(pos.get("product_id") or "")
    if st == "B" or (pid in _E4_LIQUID and st in ("", "A", "B")):
        return 4
    if st in ("C", "A"):
        return 3 if pid in _E3_LIQUID else 1
    return 1


def _engine_deployed(state: Dict[str, Any], engine: int) -> float:
    return sum(
        float(p.get("cost_usd") or 0.0)
        for p in (state.get("positions") or [])
        if int(p.get("engine") or _infer_engine_from_legacy(p)) == engine
    )


def _engine_open_count(state: Dict[str, Any], engine: int) -> int:
    return sum(
        1
        for p in (state.get("positions") or [])
        if int(p.get("engine") or _infer_engine_from_legacy(p)) == engine
    )


def _can_buy_global(
    state: Dict[str, Any], product_id: str, usd_amount: float, usd_balance: float
) -> Tuple[bool, str]:
    max_total = _max_total_exposure_usd(usd_balance)
    max_daily_loss = _max_daily_loss_usd(usd_balance)
    raw_max = os.environ.get("COINBASE_MAX_POSITIONS")
    if raw_max is not None and str(raw_max).strip() != "":
        max_open = int(float(raw_max))
    else:
        max_open = int(_env_float("COINBASE_MAX_OPEN_POSITIONS", 50.0))
    min_order = _min_order_usd(usd_balance)

    daily_loss = -(float(state.get("daily_pnl_usd") or 0.0))
    if daily_loss >= max_daily_loss:
        return False, (
            f"daily loss limit hit ({max_daily_loss/max(usd_balance,1e-9)*100:.1f}% of balance) "
            f"(${daily_loss:.2f} today)"
        )

    if usd_amount < min_order:
        return False, f"order ${usd_amount:.2f} below minimum ${min_order:.2f}"

    open_count = len(state.get("positions") or [])
    if open_count >= max_open:
        return False, f"max open positions ({max_open}) reached"

    current_exposure = _total_open_cost(state)
    if max_total > 0 and current_exposure + usd_amount > max_total:
        return False, f"would exceed max total exposure ${max_total:.2f}"

    max_deploy_pct = _env_float("COINBASE_MAX_DEPLOY_PCT", 0.80)
    if usd_balance > 0 and current_exposure + usd_amount > usd_balance * max_deploy_pct:
        return False, (
            f"max deploy {max_deploy_pct*100:.0f}% of ${usd_balance:.2f} balance would be exceeded"
        )

    cap = _max_per_coin_usd(usd_balance)
    product_cost = _product_open_cost(state, product_id)
    if cap > 0 and product_cost + usd_amount > cap:
        return False, (
            f"{product_id} cap ${cap:.0f} would be exceeded "
            f"(${product_cost:.2f} already open)"
        )

    reserve = usd_balance * _reserve_pct()
    available = usd_balance - reserve
    if usd_amount > available:
        return False, (
            f"{_reserve_pct()*100:.0f}% cash reserve: need ${usd_amount:.2f} but "
            f"only ${available:.2f} deployable (balance ${usd_balance:.2f})"
        )

    return True, "ok"


def _can_buy_engine(
    state: Dict[str, Any],
    engine: int,
    product_id: str,
    order_usd: float,
    usd_balance: float,
    max_engine_positions: int,
) -> Tuple[bool, str]:
    ok, reason = _can_buy_global(state, product_id, order_usd, usd_balance)
    if not ok:
        return False, reason
    if _has_open_product(state, product_id):
        return False, "already open on product"
    cap_q = _engine_quarter_cap(usd_balance)
    if cap_q > 0 and _engine_deployed(state, engine) + order_usd > cap_q + 1e-6:
        return False, f"engine E{engine} 25% cap ${cap_q:.2f} would be exceeded"
    if _engine_open_count(state, engine) >= max_engine_positions:
        return False, f"engine E{engine} max positions ({max_engine_positions})"
    return True, "ok"


# Exchange minimum base order size (spot USD pairs). Overwritten from GET /products/{id} on startup.
_PRODUCT_MIN_BASE_SIZE: Dict[str, float] = {
    "DOGE-USD": 0.1,
    "ADA-USD": 1.0,
    "XRP-USD": 1.0,
    "SHIB-USD": 1000.0,
    "PEPE-USD": 1000.0,
    "BTC-USD": 0.000001,
    "ETH-USD": 0.00000001,
    "SOL-USD": 0.000001,
    "AVAX-USD": 0.001,
    "DOT-USD": 0.1,
    "LINK-USD": 0.01,
    "UNI-USD": 0.01,
    "MATIC-USD": 1.0,
}

# ``base_increment`` from GET /products/{id} (optional; improves sell sizing).
_PRODUCT_BASE_INCREMENT: Dict[str, float] = {}

# Decimal places for base_size strings (floor to avoid overselling). Default 8 for unknown pairs.
_PRODUCT_BASE_PRECISION: Dict[str, int] = {
    "BTC-USD": 8,
    "ETH-USD": 8,
    "SOL-USD": 2,
    "DOGE-USD": 1,
    "ADA-USD": 6,
    "XRP-USD": 6,
    "LINK-USD": 4,
    "DOT-USD": 2,
    "AVAX-USD": 4,
    "UNI-USD": 4,
    "MATIC-USD": 2,
    "SHIB-USD": 0,
    "PEPE-USD": 0,
}


def _min_base_size_for_product(pid: str) -> float:
    return float(_PRODUCT_MIN_BASE_SIZE.get(pid, 0.000001))


def _enforce_min_base_for_sell(pid: str, base_size: float) -> float:
    """Return base_size if it meets the exchange minimum; else 0 (caller must skip sell)."""
    if base_size <= 0:
        return 0.0
    min_sz = _min_base_size_for_product(pid)
    if base_size + 1e-18 < min_sz:
        logger.warning(
            "Position base size %s < exchange min order %s for %s — skip sell",
            base_size,
            min_sz,
            pid,
        )
        return 0.0
    return base_size


def _fmt_base_size(product_id: str, base_size: float) -> str:
    """Floor ``base_size`` to product precision, then snap down to ``base_increment`` when known."""
    precision = int(_PRODUCT_BASE_PRECISION.get(product_id, 8))
    q = Decimal("1").scaleb(-precision)
    # ``str(float)`` avoids binary float artifacts from ``format(..., ".18f")`` (e.g. 0.6 → 0.5999…).
    d = Decimal(str(base_size)).quantize(q, rounding=ROUND_DOWN)
    inc = float(_PRODUCT_BASE_INCREMENT.get(product_id) or 0.0)
    if inc > 0:
        inc_d = Decimal(str(inc))
        if inc_d > 0:
            n = (d / inc_d).quantize(Decimal(1), rounding=ROUND_DOWN)
            d = (n * inc_d).quantize(q, rounding=ROUND_DOWN)
    return format(d, f".{precision}f")


def _parse_csv_products(raw: Optional[str]) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def _hf_scan_product_ids(client: CoinbaseClient, state: Dict[str, Any], now: float) -> List[str]:
    max_n = max(10, int(_env_float("COINBASE_HF_MAX_SCAN_PRODUCTS", 2500.0)))
    cache_ttl = _env_float("COINBASE_HF_PRODUCT_LIST_CACHE_SEC", 60.0)
    csv = (os.environ.get("COINBASE_HF_SCAN_PRODUCTS") or "").strip()
    if csv:
        return _parse_csv_products(csv)[:max_n]
    last = float(state.get("hf_product_cache_ts") or 0.0)
    if now - last >= cache_ttl or not state.get("hf_product_cache"):
        try:
            rows = client.list_exchange_usd_products()
            pids = [str(r.get("product_id") or "") for r in rows if r.get("product_id")]
            pids = [p for p in pids if p.endswith("-USD")][:max_n]
            state["hf_product_cache"] = pids
            state["hf_product_cache_ts"] = now
            logger.info("Coinbase: Exchange /products — tracking %d USD pairs", len(pids))
        except Exception as exc:
            logger.warning("Coinbase HF Exchange product list: %s", exc)
    return list(state.get("hf_product_cache") or [])


def collect_price_product_ids(state: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for p in state.get("positions") or []:
        pid = p.get("product_id")
        if pid:
            ids.append(str(pid))
    return ids


class CoinbaseAccumulator:
    """Four Coinbase engines (E1–E4) with unified exits and capital split.

    Read open positions via :meth:`get_state` / :attr:`state` (backed by
    ``load_coinbase_state()``). When running one-off scripts from the repo, use
    ``PYTHONPATH=src`` so you import this module and not an older site-packages copy.
    """

    def __init__(self, client: Optional[CoinbaseClient] = None) -> None:
        self._client = client or CoinbaseClient()
        self._rate_limiter = _RateLimiter()
        self._price_history: Dict[str, deque[Tuple[float, float]]] = {}
        self._last_ac_tick: float = -1e9
        self._stats_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._buys_this_scan = 0
        self._brokerage_vol_cache: Dict[str, float] = {}
        self._brokerage_vol_cache_ts: float = 0.0
        self._load_product_min_sizes()

    def _load_product_min_sizes(self) -> None:
        """Refresh :data:`_PRODUCT_MIN_BASE_SIZE` from ``GET /products/{product_id}``."""
        for pid in sorted(_PRODUCT_MIN_BASE_SIZE.keys()):
            try:
                j = self._client._request("GET", f"/products/{pid}")
                if not isinstance(j, dict):
                    continue
                raw = j.get("base_min_size")
                if raw is not None:
                    try:
                        min_s = float(raw)
                        if min_s > 0:
                            _PRODUCT_MIN_BASE_SIZE[pid] = min_s
                            logger.info("Exchange min base size %s: %s", pid, min_s)
                    except (TypeError, ValueError):
                        pass
                raw_inc = j.get("base_increment")
                if raw_inc is not None:
                    try:
                        incf = float(raw_inc)
                        if incf > 0:
                            _PRODUCT_BASE_INCREMENT[pid] = incf
                            logger.info("Exchange base increment %s: %s", pid, incf)
                    except (TypeError, ValueError):
                        pass
            except Exception as exc:
                logger.debug("Min size fetch %s: %s", pid, exc)

    def get_state(self) -> Dict[str, Any]:
        """Latest persisted Coinbase state (same as ``load_coinbase_state()``)."""
        return load_coinbase_state()

    @property
    def state(self) -> Dict[str, Any]:
        return self.get_state()

    def _ingest_prices_into_history(
        self, prices: Dict[str, Tuple[float, float]], now: float
    ) -> None:
        keep = int(_env_float("COINBASE_PRICE_HISTORY_SEC", 4200.0))
        for pid, (bid, ask) in prices.items():
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = self._price_history.setdefault(pid, deque())
            hist.append((now, mid))
            cutoff = now - keep
            while hist and hist[0][0] < cutoff:
                hist.popleft()

    def _brokerage_volume_map_24h(self, now: float) -> Dict[str, float]:
        ttl = _env_float("COINBASE_BROKERAGE_VOL_CACHE_SEC", 55.0)
        if self._brokerage_vol_cache and (now - self._brokerage_vol_cache_ts) < ttl:
            return self._brokerage_vol_cache
        try:
            rows = self._client.list_brokerage_products()
        except Exception as exc:
            logger.warning("Coinbase brokerage products (volume map): %s", exc)
            return self._brokerage_vol_cache
        self._brokerage_vol_cache = {
            str(r.get("product_id") or ""): _quote_volume_24h_usd(r)
            for r in rows
            if r.get("product_id")
        }
        self._brokerage_vol_cache_ts = now
        return self._brokerage_vol_cache

    def _buy_preflight_ok(
        self, pid: str, prices: Dict[str, Tuple[float, float]], now: float
    ) -> Tuple[bool, str]:
        """Liquidity screen before any market buy: min price, spread, 24h volume."""
        min_px = _min_product_price_usd()
        min_vol = _min_volume_usd_24h()
        max_spread = _max_buy_spread_ratio()
        if pid not in prices:
            return False, "no_price"
        bid, ask = prices[pid]
        try:
            bid_f = float(bid or 0.0)
            ask_f = float(ask or 0.0)
        except (TypeError, ValueError):
            return False, "bad_quote"
        if bid_f <= min_px:
            logger.debug("Skip %s: bid %.6f at or below min price %.4f", pid, bid_f, min_px)
            return False, "price_low"
        mid = (bid_f + ask_f) / 2.0 if bid_f > 0 and ask_f > 0 else bid_f
        if mid < min_px:
            logger.debug("Skip %s: mid %.6f below min price %.4f", pid, mid, min_px)
            return False, "mid_low"
        if ask_f > 0 and bid_f > 0:
            spread = (ask_f - bid_f) / bid_f
            if spread > max_spread:
                logger.debug(
                    "Skip %s: spread %.4f > max %.4f", pid, spread, max_spread
                )
                return False, "spread_wide"
        vm = self._brokerage_volume_map_24h(now)
        qv = float(vm.get(pid) or 0.0)
        if qv < min_vol:
            logger.debug(
                "Skip %s: 24h quote vol %.0f < min %.0f", pid, qv, min_vol
            )
            return False, "volume_low"
        return True, "ok"

    def _cached_exchange_stats(
        self, product_id: str, now: float
    ) -> Optional[Dict[str, Any]]:
        ttl = _env_float("COINBASE_E2_STATS_CACHE_SEC", 55.0)
        ent = self._stats_cache.get(product_id)
        if ent and (now - ent[0]) < ttl:
            return ent[1]
        st = self._client.get_exchange_product_stats(product_id)
        if st:
            self._stats_cache[product_id] = (now, st)
            if len(self._stats_cache) > 600:
                for k in list(self._stats_cache.keys())[:100]:
                    self._stats_cache.pop(k, None)
        return st

    def _gh_volume_ratio(
        self, state: Dict[str, Any], pid: str, stats_volume: float, now: float
    ) -> float:
        track = state.setdefault("gh_vol_track", {}).setdefault(pid, [])
        v = float(stats_volume or 0.0)
        track.append((now, v))
        track[:] = [(t, x) for t, x in track if now - t < 3600.0]
        if len(track) < 3:
            return 999.0
        rates: List[float] = []
        for i in range(1, len(track)):
            dt = track[i][0] - track[i - 1][0]
            if 0.5 < dt < 400.0:
                dv = abs(track[i][1] - track[i - 1][1])
                rates.append(dv / max(dt / 60.0, 0.01))
        if len(rates) < 2:
            return 999.0
        recent = sum(rates[-3:]) / float(min(3, len(rates[-3:])))
        baseline = sum(rates) / float(len(rates))
        if baseline < 1e-9:
            return 999.0
        return recent / baseline

    def _e2_candidates(
        self, state: Dict[str, Any], prices: Dict[str, Tuple[float, float]], now: float
    ) -> List[Dict[str, Any]]:
        min_pct = _env_float(
            "COINBASE_GAINER_MIN_PCT",
            _env_float("COINBASE_E2_MIN_HOUR_PCT", 0.01),
        )
        min_vol_usd = max(
            _min_volume_usd_24h(),
            _env_float("COINBASE_E2_MIN_VOLUME_USD", 10_000.0),
        )
        min_px = _min_product_price_usd()
        min_vol_ratio = _env_float(
            "COINBASE_GAINER_VOL_RATIO",
            _env_float("COINBASE_E2_MIN_VOL_RATIO", 1.2),
        )
        max_stats = max(20, int(_env_float("COINBASE_E2_MAX_STATS_FETCH", 80.0)))
        vol_map = self._brokerage_volume_map_24h(now)
        cd = state.setdefault("hunter_cooldown", {})
        cooldown_sec = _env_float("COINBASE_E2_COOLDOWN_SEC", 1800.0)
        rough: List[Dict[str, Any]] = []
        for pid, (bid, ask) in prices.items():
            if not str(pid).endswith("-USD") or bid <= 0 and ask <= 0:
                continue
            lt = float(cd.get(pid) or 0.0)
            if lt > 0 and (now - lt) < cooldown_sec:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            if mid < min_px:
                continue
            hist = list(self._price_history.get(pid) or [])
            p60 = _mid_at_or_before(hist, now - 3600.0)
            if p60 is None or p60 <= 0:
                continue
            hour_gr = (mid - p60) / p60
            if hour_gr < min_pct:
                continue
            if float(vol_map.get(pid) or 0.0) < min_vol_usd:
                continue
            rough.append({"product_id": pid, "mid": mid, "hour_pct": hour_gr})
        rough.sort(key=lambda x: -x["hour_pct"])
        out: List[Dict[str, Any]] = []
        for row in rough[:max_stats]:
            pid = row["product_id"]
            st = self._cached_exchange_stats(pid, now)
            if not st:
                continue
            try:
                last = float(st.get("last") or 0.0)
                high = float(st.get("high") or 0.0)
                o = float(st.get("open") or 0.0)
                sv = float(st.get("volume") or 0.0)
            except (TypeError, ValueError):
                continue
            if high > 0 and last >= high * 0.999:
                continue
            if o > 0 and (last - o) / o >= _env_float("COINBASE_E2_SKIP_24H_PCT", 0.50):
                continue
            vr = self._gh_volume_ratio(state, pid, sv, now)
            if vr < 500.0 and vr < min_vol_ratio:
                continue
            out.append({**row, "vol_ratio": vr})
        out.sort(key=lambda x: (-x["hour_pct"], -x["vol_ratio"]))
        return out

    def _detect_early_movers(self) -> List[Dict[str, Any]]:
        state = load_coinbase_state()
        now = time.time()
        scan_ids = _hf_scan_product_ids(self._client, state, now)
        pids = list(set(scan_ids) | set(collect_price_product_ids(state)))
        try:
            prices = self._client.get_prices_batched(pids)
        except Exception as exc:
            logger.warning("Coinbase _detect_early_movers: %s", exc)
            return []
        if not prices:
            return []
        self._ingest_prices_into_history(prices, now)
        return self._e2_candidates(state, prices, now)

    def scan_and_trade(self) -> Optional[Dict[str, Any]]:
        if not coinbase_enabled():
            return None
        if not self._client.has_credentials():
            logger.debug("Coinbase: no credentials configured, scan skipped")
            return None
        try:
            return self._scan()
        except CoinbaseAuthError as exc:
            logger.error("Coinbase auth error (check keys): %s", exc)
            return None
        except Exception as exc:
            logger.warning("Coinbase scan error (non-fatal): %s", exc)
            return None

    def get_summary(self) -> Dict[str, Any]:
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)
        positions = state.get("positions") or []
        by_product: Dict[str, int] = {}
        for pos in positions:
            pid = pos.get("product_id") or "?"
            by_product[pid] = by_product.get(pid, 0) + 1
        return {
            "enabled": coinbase_enabled(),
            "open_count": len(positions),
            "total_cost_usd": _total_open_cost(state),
            "daily_pnl_usd": float(state.get("daily_pnl_usd") or 0.0),
            "total_realized_usd": float(state.get("total_realized_usd") or 0.0),
            "total_trades": int(state.get("total_trades") or 0),
            "wins": int(state.get("wins") or 0),
            "losses": int(state.get("losses") or 0),
            "by_product": by_product,
        }

    def load_and_check_positions_on_startup(self) -> None:
        if not coinbase_enabled():
            return
        if not self._client.has_credentials():
            return
        logger.info("Coinbase: startup exit check …")
        try:
            state = load_coinbase_state()
            _reset_daily_pnl_if_needed(state)
            n_open = len(state.get("positions") or [])
            logger.info(
                "Coinbase startup: %d open position(s) from %s",
                n_open,
                _positions_path(),
            )
            now = time.time()
            scan_ids = _hf_scan_product_ids(self._client, state, now)
            pids = list(set(scan_ids) | set(collect_price_product_ids(state)))
            prices = self._client.get_prices_batched(pids)
            if not prices:
                logger.warning("Coinbase startup: no prices")
                return
            self._ingest_prices_into_history(prices, now)
            _ = self._check_exits(state, prices, now)
            save_coinbase_state(state)
            logger.info(
                "Coinbase startup: %d positions after exit scan",
                len(state.get("positions") or []),
            )
        except Exception as exc:
            logger.warning("Coinbase startup check failed (non-fatal): %s", exc)

    def force_sell_all_positions(self) -> int:
        """Market-sell every open position immediately and clear local position list.

        Used on **every** process startup (before normal exit scans) and for manual recovery.
        Never overwrites ``entry_time`` on rows — ages logged from saved state only.
        Attempts market sells even when bid cache is empty or a product is missing from
        the batch quote map (Coinbase may still fill from book). Does not use the buy
        rate limiter. Failed sells leave ``sell_pending`` on retained rows.
        """
        if not coinbase_enabled():
            return 0
        if not self._client.has_credentials():
            return 0
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)
        positions = list(state.get("positions") or [])
        logger.info("FORCE SELL ALL: %d position(s)", len(positions))
        if not positions:
            return 0

        now = time.time()
        scan_ids = _hf_scan_product_ids(self._client, state, now)
        pids = list(
            set(scan_ids)
            | set(collect_price_product_ids(state))
            | {str(p.get("product_id") or "") for p in positions}
        )
        prices: Dict[str, Tuple[float, float]] = {}
        try:
            prices = self._client.get_prices_batched(pids) or {}
        except Exception as exc:
            logger.warning("FORCE SELL ALL: price fetch failed: %s — blind sells only", exc)
        if prices:
            self._ingest_prices_into_history(prices, now)
        else:
            logger.warning("FORCE SELL ALL: no price map — attempting market sells anyway")

        sold = 0
        kept: List[Dict[str, Any]] = []
        for pos in positions:
            pid = str(pos.get("product_id") or "")
            entry = float(pos.get("entry_price") or 0.0)
            size_base = float(pos.get("size_base") or 0.0)
            cost_usd = float(pos.get("cost_usd") or 0.0)
            entry_t = float(pos.get("entry_time") or 0.0)
            eng = int(pos.get("engine") or _infer_engine_from_legacy(pos))
            age = (now - entry_t) if entry_t > 0 else -1.0
            if not pid or size_base <= 0:
                logger.warning("FORCE SELL ALL: skip invalid %s size=%s", pid, size_base)
                kept.append(pos)
                continue

            quote = prices.get(pid)
            if quote and len(quote) >= 2:
                try:
                    current = float(quote[0] or 0.0)
                except (TypeError, ValueError):
                    current = 0.0
            else:
                current = 0.0
            pnl_pct = 0.0 if entry <= 0 or current <= 0 else (current - entry) / entry
            logger.info("FORCE SELL: %s age=%.0fs (entry_time from state)", pid, age)
            base_str = _fmt_base_size(pid, size_base)
            try:
                result = self._try_market_sell_twice(
                    pid, base_str, size_base_from_pos=size_base
                )
            except Exception as exc:
                logger.warning("FORCE SELL ALL: exception %s: %s", pid, exc)
                upd = dict(pos)
                upd["sell_pending"] = True
                kept.append(upd)
                continue

            profit_usd = (
                -float(cost_usd) if current <= 0 else current * size_base - cost_usd
            )
            if result is not None and result.success:
                sold += 1
                state["daily_pnl_usd"] = float(state.get("daily_pnl_usd") or 0.0) + profit_usd
                state["total_realized_usd"] = (
                    float(state.get("total_realized_usd") or 0.0) + profit_usd
                )
                state["total_trades"] = int(state.get("total_trades") or 0) + 1
                if profit_usd >= 0:
                    state["wins"] = int(state.get("wins") or 0) + 1
                else:
                    state["losses"] = int(state.get("losses") or 0) + 1
                if eng == 2:
                    state.setdefault("hunter_cooldown", {})[pid] = now
                logger.info(
                    "FORCE SOLD: %s ok=%s pnl=%+.2f%% $%.4f",
                    pid,
                    getattr(result, "success", False),
                    pnl_pct * 100.0,
                    profit_usd,
                )
                _log_trade(
                    {
                        "ts": now,
                        "type": "sell",
                        "engine": eng,
                        "reason": "force_sell_all",
                        "order_id": getattr(result, "order_id", ""),
                        "product_id": pid,
                        "entry_price": entry,
                        "exit_price": current,
                        "pnl_usd": profit_usd,
                    }
                )
                try:
                    from trading_ai.shark.reporting import send_telegram

                    send_telegram(
                        f"🚨 FORCE CB exit: {_cb_sym(pid)} "
                        f"{pnl_pct*100:+.2f}% ${profit_usd:+.3f}"
                    )
                except Exception:
                    pass
            elif self._maybe_remove_phantom_after_failed_sell(state, pid, result):
                pass
            else:
                logger.warning(
                    "FORCE SELL failed %s (%s) — sell_pending=True",
                    pid,
                    getattr(result, "reason", "?"),
                )
                upd = dict(pos)
                upd["sell_pending"] = True
                kept.append(upd)

        state["positions"] = kept
        save_coinbase_state(state)
        logger.info("FORCE SELL COMPLETE: sold=%d remaining_rows=%d", sold, len(kept))
        return sold

    def emergency_clear_stale_positions(self) -> int:
        """Restart flush: ``time_stop_sec = 0`` → every position is stale; sell all via force.

        Delegates to :meth:`force_sell_all_positions` only — never mutates ``entry_time``
        (always uses timestamps already stored on each position row).
        """
        if not coinbase_enabled():
            return 0
        if not self._client.has_credentials():
            return 0
        # Treat time stop as zero seconds so *all* positions qualify as stale.
        time_stop_sec = 0.0
        logger.info(
            "Coinbase EMERGENCY: time_stop_sec=%.0f — delegating to force sell all",
            time_stop_sec,
        )
        return self.force_sell_all_positions()

    def _verify_holdings(self, product_id: str, required_size: float) -> float:
        """Return spendable base balance for ``product_id``; ``0`` if none. On API error, fail open."""
        currency = product_id.replace("-USD", "")
        try:
            for a in self._client.get_accounts():
                if not isinstance(a, dict):
                    continue
                if str(a.get("currency") or "").upper() != currency.upper():
                    continue
                # Sells must use *available* only — ``balance`` can exceed what is spendable
                # (open orders / holds), and max(available, balance) caused INSUFFICIENT_FUND.
                ab = a.get("available_balance")
                if not isinstance(ab, dict):
                    ab = {}
                available = float(ab.get("value", 0) or 0)
                logger.warning(
                    "HOLDINGS: %s available=%.8f required=%.8f",
                    product_id,
                    available,
                    required_size,
                )
                return max(0.0, available)
        except Exception as exc:
            logger.warning("Holdings check failed: %s", exc)
            return float(required_size)
        return 0.0

    def _maybe_remove_phantom_after_failed_sell(
        self, state: Dict[str, Any], product_id: str, result: Any
    ) -> bool:
        if result is None:
            return False
        reason = str(getattr(result, "reason", None) or "")
        if reason not in ("below_min_size", "insufficient_holdings"):
            return False
        logger.warning(
            "PHANTOM POSITION: %s not in Coinbase — removing from state (reason=%s)",
            product_id,
            reason,
        )
        state["positions"] = [
            p
            for p in (state.get("positions") or [])
            if str(p.get("product_id") or "") != product_id
        ]
        save_coinbase_state(state)
        return True

    def _try_market_sell_twice(
        self,
        product_id: str,
        base_size: str,
        size_base_from_pos: float = 0.0,
    ) -> OrderResult:
        """Market sell with diagnostics; two attempts, 0.5s between."""
        try:
            size_float = float(base_size)
        except (ValueError, TypeError):
            logger.warning("SELL FAIL: %s invalid size=%s", product_id, base_size)
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="error",
                outlet="coinbase",
                success=False,
                reason="invalid_size",
                raw={"base_size": base_size, "size_base_from_pos": size_base_from_pos},
            )

        enforced = _enforce_min_base_for_sell(product_id, size_float)
        if enforced <= 0:
            logger.warning(
                "SELL SKIP: %s size=%.8f below exchange minimum",
                product_id,
                size_float,
            )
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="skipped",
                outlet="coinbase",
                success=False,
                reason="below_min_size",
                raw={
                    "size_float": size_float,
                    "size_base_from_pos": size_base_from_pos,
                },
            )

        available = self._verify_holdings(product_id, enforced)
        if available < enforced * 0.5:
            logger.warning(
                "SELL SKIP: %s available=%.8f needed=%.8f — not enough to sell",
                product_id,
                available,
                enforced,
            )
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="skipped",
                outlet="coinbase",
                success=False,
                reason="insufficient_holdings",
                raw={
                    "available": available,
                    "enforced": enforced,
                    "size_base_from_pos": size_base_from_pos,
                },
            )

        if available < enforced:
            enforced = available
            enforced = _enforce_min_base_for_sell(product_id, enforced)
            if enforced <= 0:
                logger.warning(
                    "SELL SKIP: %s after cap to available, size below exchange minimum",
                    product_id,
                )
                return OrderResult(
                    order_id="",
                    filled_price=0.0,
                    filled_size=0.0,
                    timestamp=time.time(),
                    status="skipped",
                    outlet="coinbase",
                    success=False,
                    reason="below_min_size",
                    raw={"available": available, "size_base_from_pos": size_base_from_pos},
                )
            logger.warning(
                "SELL ADJUSTED: %s using available=%.8f",
                product_id,
                enforced,
            )

        base_str = _fmt_base_size(product_id, enforced)
        logger.warning(
            "SELL: %s size_raw=%s size_enforced=%.8f size_formatted=%s pos_hint=%s",
            product_id,
            base_size,
            enforced,
            base_str,
            size_base_from_pos,
        )

        last: Optional[OrderResult] = None
        for attempt in range(1, 3):
            try:
                result = self._client.place_market_sell(product_id, base_str)
                last = result
                logger.warning(
                    "SELL ATTEMPT %d/2: %s success=%s reason=%s order_id=%s",
                    attempt,
                    product_id,
                    result.success if result else None,
                    result.reason if result else "None",
                    result.order_id if result else "None",
                )
                if result and result.success:
                    return result
                logger.warning(
                    "SELL FAILED %d/2: %s reason=%s",
                    attempt,
                    product_id,
                    result.reason if result else "no result",
                )
            except Exception as exc:
                logger.warning(
                    "SELL EXCEPTION %d/2: %s error=%s type=%s",
                    attempt,
                    product_id,
                    str(exc),
                    type(exc).__name__,
                )
                last = None
            if attempt == 1:
                time.sleep(0.5)
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="error",
            outlet="coinbase",
            success=False,
            reason="all_attempts_failed",
            raw={"last_reason": getattr(last, "reason", None) if last else None},
        )

    def _get_prices_for_positions(
        self, state: Dict[str, Any]
    ) -> Dict[str, Tuple[float, float]]:
        pids = collect_price_product_ids(state)
        if not pids:
            return {}
        try:
            prices = self._client.get_prices_batched(pids)
        except Exception as exc:
            logger.warning("Coinbase exit-check price fetch failed: %s", exc)
            return {}
        missing = [x for x in pids if x and x not in prices]
        if missing:
            try:
                extra = self._client.get_prices_batched(missing)
                prices.update(extra)
            except Exception as exc:
                logger.warning("Coinbase exit-check price salvage: %s", exc)
        return prices

    def _check_exits_only(self) -> int:
        if not coinbase_enabled():
            return 0
        if not self._client.has_credentials():
            return 0
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)
        now = time.time()
        prices = self._get_prices_for_positions(state)
        exits = self._check_exits(state, prices, now)
        save_coinbase_state(state)
        return exits

    def _run_exits_only(self) -> int:
        """Scheduler ``coinbase_exit_check`` (~10s): trail / TP / SL / max hold / dawn — no buys."""
        if not coinbase_enabled():
            return 0
        if not self._client.has_credentials():
            return 0
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)
        now = time.time()
        exits = 0
        if state.get("positions"):
            prices = self._get_prices_for_positions(state)
            exits = self._check_exits(state, prices, now)
            save_coinbase_state(state)

        return exits

    def _calculate_signals(self, pid: str, _now: float = 0.0) -> Dict[str, Any]:
        """RSI, MACD line, MACD histogram proxy, EMA 9/21 — Gate A entry + Gate E screening."""
        history = list(self._price_history.get(pid) or [])
        empty: Dict[str, Any] = {
            "signal": "none",
            "rsi": 50.0,
            "ema9": 0.0,
            "ema21": 0.0,
            "macd": 0.0,
            "macd_hist": 0.0,
            "bullish_cross": False,
            "bearish_cross": False,
            "rsi_neutral": False,
        }
        if len(history) < 21:
            return dict(empty)

        prices = [float(h[1]) for h in history[-80:]]
        if len(prices) < 26:
            return dict(empty)

        ema9 = _ema_last(prices, 9)
        ema21 = _ema_last(prices, 21)
        ema9_prev = _ema_last(prices[:-1], 9)
        ema21_prev = _ema_last(prices[:-1], 21)
        bullish_cross = ema9 > ema21 and ema9_prev <= ema21_prev
        bearish_cross = ema9 < ema21 and ema9_prev >= ema21_prev

        rsi = _rsi_last(prices)
        macd, macd_prev = _macd_line_last(prices)
        macd_hist = macd - macd_prev

        rsi_neutral = 35.0 <= rsi <= 65.0
        macd_positive = macd > 0.0

        if bullish_cross and rsi_neutral:
            signal = "buy"
        elif bearish_cross and rsi_neutral:
            signal = "wait"
        elif rsi < 35.0 and macd_positive:
            signal = "buy"
        else:
            signal = "none"

        return {
            "signal": signal,
            "rsi": rsi,
            "ema9": ema9,
            "ema21": ema21,
            "macd": macd,
            "macd_hist": macd_hist,
            "bullish_cross": bullish_cross,
            "bearish_cross": bearish_cross,
            "rsi_neutral": rsi_neutral,
        }

    def _check_hedge_opportunities(
        self, state: Dict[str, Any], prices: Dict[str, Tuple[float, float]]
    ) -> int:
        """Delta-neutral partial exit: sell 50% when unrealized PnL ≥ ``COINBASE_HEDGE_TRIGGER_PCT``."""
        if not _env_bool("COINBASE_HEDGE_ENABLED", True):
            return 0
        hedge_trigger = _env_float("COINBASE_HEDGE_TRIGGER_PCT", 0.001)
        hedged = 0
        positions = state.get("positions") or []
        now = time.time()

        for pos in positions:
            if pos.get("hedge_done"):
                continue
            if pos.get("exit_submitted"):
                continue

            pid = str(pos.get("product_id") or "")
            if not pid or pid not in prices:
                continue
            bid, _ask = prices[pid]
            if not bid or bid <= 0:
                continue
            entry = float(pos.get("entry_price") or 0.0)
            if not entry:
                continue
            pnl_pct = (float(bid) - entry) / entry
            if pnl_pct < hedge_trigger:
                continue

            size_base = float(pos.get("size_base") or 0.0)
            cost_usd = float(pos.get("cost_usd") or 0.0)
            if size_base <= 0 or cost_usd <= 0:
                continue

            hedge_size = size_base * 0.5
            base_str = _fmt_base_size(pid, hedge_size)
            gate = str(pos.get("gate") or "").upper()

            logger.info(
                "HEDGE: %s up %.4f%% → selling 50%% to lock profit",
                pid,
                pnl_pct * 100.0,
            )
            result = self._try_market_sell_twice(
                pid, base_str, size_base_from_pos=hedge_size
            )
            if result is None or not result.success:
                if self._maybe_remove_phantom_after_failed_sell(state, pid, result):
                    pass
                else:
                    logger.warning("HEDGE: sell failed %s", pid)
                continue

            hedged += 1
            pos["hedge_done"] = True
            pos["hedge_pct"] = pnl_pct
            pos["hedge_ts"] = now
            pos["size_base"] = size_base - hedge_size
            pos["cost_usd"] = cost_usd * 0.5
            pos["hedge_locked_usd"] = float(bid) * hedge_size - cost_usd * 0.5
            save_coinbase_state(state)
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"🔒 HEDGE LOCKED: {pid} +{pnl_pct * 100:.3f}% → 50% sold, profit secured"
                    + (f" Gate {gate}" if gate else "")
                )
            except Exception:
                pass
            logger.info("HEDGE SUCCESS: %s — remaining 50%% still open", pid)

        return hedged

    def _time_stop_limit_sec(self, pos: Dict[str, Any]) -> float:
        """Seconds until time-stop for this position (Gates A–C or engine E1–E4)."""
        eng = int(pos.get("engine") or _infer_engine_from_legacy(pos))
        gate = str(pos.get("gate") or "").strip().upper()
        if gate == "A":
            return max(0.0, float(GATE_A_MAX_HOLD_MIN) * 60.0)
        if gate == "B":
            return max(0.0, float(GATE_B_MAX_HOLD_MIN) * 60.0)
        if gate == "C":
            return max(0.0, float(GATE_C_MAX_HOLD_MIN) * 60.0)
        _tp, _sl, tmin, _trail = self._exit_params(eng)
        return max(0.0, float(tmin) * 60.0)

    def _migrate_expiry_fields(self, state: Dict[str, Any], now: float) -> bool:
        """Backfill ``expiry_time`` / ``must_sell_by`` from ``entry_time`` + time stop."""
        changed = False
        out: List[Dict[str, Any]] = []
        for pos in state.get("positions") or []:
            p = dict(pos)
            ex = p.get("expiry_time")
            ms = p.get("must_sell_by")
            if ex is not None and ms is None:
                p["must_sell_by"] = float(ex)
                changed = True
            elif ms is not None and ex is None:
                p["expiry_time"] = float(ms)
                changed = True
            if p.get("expiry_time") is not None:
                out.append(p)
                continue
            tlim = self._time_stop_limit_sec(p)
            entry_t = float(p.get("entry_time") or 0.0)
            if entry_t > 0:
                p["expiry_time"] = entry_t + tlim
            else:
                p["expiry_time"] = now
            p["must_sell_by"] = p["expiry_time"]
            changed = True
            out.append(p)
        if changed:
            state["positions"] = out
        return changed

    def _any_position_expired(self, state: Dict[str, Any], now: float) -> bool:
        """True if any open position is past persisted ``expiry_time`` or age-based time stop."""
        for pos in state.get("positions") or []:
            tlim = self._time_stop_limit_sec(pos)
            if tlim <= 0:
                continue
            ex = float(pos.get("expiry_time") or pos.get("must_sell_by") or 0.0)
            if ex > 0 and now >= ex:
                return True
            if ex <= 0:
                et = float(pos.get("entry_time") or 0.0)
                if et <= 0:
                    et = now - 600.0
                if now - et >= tlim:
                    return True
        return False

    def _exit_params(self, engine: int) -> Tuple[float, float, float, float]:
        """Returns (profit_pct, stop_pct, time_min, trail_pct). Unified TP/SL; trail only for E2."""
        tp = _env_float("COINBASE_PROFIT_TARGET_PCT", 0.0015)
        sl = _env_float("COINBASE_STOP_LOSS_PCT", 0.0003)
        tmin = _env_float("COINBASE_TIME_STOP_MIN", 3.0)
        if engine == 2:
            return (
                tp,
                sl,
                tmin,
                _env_float("COINBASE_E2_TRAIL_PCT", 0.0003),
            )
        return (tp, sl, tmin, 0.0)

    def _check_exits(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        now: float,
    ) -> int:
        if self._migrate_expiry_fields(state, now):
            save_coinbase_state(state)
        positions_snapshot = list(state.get("positions") or [])
        remaining: List[Dict[str, Any]] = []
        exits = 0
        logger.info("Exit check: %d open position(s)", len(positions_snapshot))
        try:
            from zoneinfo import ZoneInfo

            now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now_et = datetime.now(timezone(timedelta(hours=-4)))

        for idx, pos in enumerate(positions_snapshot):
            pid = str(pos.get("product_id") or "")
            entry = float(pos.get("entry_price") or 0.0)
            size_base = float(pos.get("size_base") or 0.0)
            cost_usd = float(pos.get("cost_usd") or 0.0)
            entry_t = float(pos.get("entry_time") or 0.0)
            if not entry_t or entry_t <= 0:
                entry_t = now - 600.0
                logger.warning(
                    "CB CHECK: %s missing/zero entry_time — synthetic age ~10m for time-stop",
                    pid,
                )
            eng = int(pos.get("engine") or _infer_engine_from_legacy(pos))
            sell_pending = bool(pos.get("sell_pending"))
            gate = str(pos.get("gate") or "").strip().upper()

            if size_base <= 0:
                remaining.append(pos)
                continue

            quote = prices.get(pid)
            if quote is None or len(quote) < 2:
                bid = 0.0
                ask = 0.0
            else:
                try:
                    bid = float(quote[0] or 0.0)
                    ask = float(quote[1] or 0.0)
                except (TypeError, ValueError):
                    bid = 0.0
                    ask = 0.0
            current = bid
            no_price = quote is None or current <= 0

            if not no_price and entry <= 0:
                remaining.append(pos)
                continue

            pnl_pct = 0.0 if no_price else (current - entry) / entry
            age_s = int(now - entry_t)
            age = now - entry_t
            log_label = f"Gate {gate}" if _is_abc_gate(gate) else f"E{eng}"
            logger.info(
                "CB CHECK %s: %s pnl=%s age=%ds pending=%s no_price=%s",
                log_label,
                pid,
                "n/a" if no_price else f"{pnl_pct * 100.0:+.3f}%",
                age_s,
                sell_pending,
                no_price,
            )

            # ── Gates A/B/C: trail, targets, mission exits ─────────────────
            if _is_abc_gate(gate):
                upd = dict(pos)
                sell_reason = ""
                if sell_pending:
                    sell_reason = "sell_pending_retry"
                elif no_price:
                    sell_reason = "no_price_stop"
                else:
                    trail_pct = {
                        "A": GATE_A_TRAIL_STOP,
                        "B": GATE_B_TRAIL_STOP,
                        "C": GATE_C_TRAIL_STOP,
                    }[gate]
                    prev_peak = float(pos.get("peak_price") or entry)
                    if current > prev_peak:
                        upd["peak_price"] = current
                        upd["trail_stop"] = current * (1.0 - trail_pct)
                    else:
                        upd["peak_price"] = prev_peak
                        upd["trail_stop"] = float(
                            pos.get("trail_stop") or (prev_peak * (1.0 - trail_pct))
                        )
                    trail_line = float(upd["trail_stop"])
                    if gate == "A":
                        profit_target = GATE_A_PROFIT_TARGET
                        stop_loss = GATE_A_STOP_LOSS
                        max_hold_s = GATE_A_MAX_HOLD_MIN * 60.0
                        min_hold_s = 0.0
                    elif gate == "B":
                        profit_target = GATE_B_PROFIT_TARGET
                        stop_loss = GATE_B_STOP_LOSS
                        max_hold_s = GATE_B_MAX_HOLD_MIN * 60.0
                        min_hold_s = GATE_B_MIN_HOLD_MIN * 60.0
                    else:
                        profit_target = GATE_C_PROFIT_TARGET
                        stop_loss = GATE_C_STOP_LOSS
                        max_hold_s = GATE_C_MAX_HOLD_MIN * 60.0
                        min_hold_s = GATE_C_MIN_HOLD_MIN * 60.0

                    mhu = float(pos.get("min_hold_until") or 0.0)
                    if mhu > 0:
                        min_hold_s = max(min_hold_s, max(0.0, mhu - entry_t))

                    if gate == "A" and now_et.hour == 8:
                        sell_reason = "dawn_sweep"
                    elif current <= trail_line:
                        sell_reason = "trail_stop"
                    elif pnl_pct <= -stop_loss:
                        sell_reason = "stop_loss"
                    elif age >= min_hold_s:
                        if pnl_pct >= profit_target:
                            sell_reason = "profit_target"
                        elif age >= max_hold_s:
                            sell_reason = "timeout"

                if not sell_reason:
                    remaining.append(upd)
                    continue

                base_str = _fmt_base_size(pid, size_base)
                result = self._try_market_sell_twice(
                    pid, base_str, size_base_from_pos=size_base
                )
                profit_usd = (
                    -float(cost_usd) if no_price else current * size_base - cost_usd
                )

                if result is not None and result.success:
                    exits += 1
                    state["daily_pnl_usd"] = float(state.get("daily_pnl_usd") or 0.0) + profit_usd
                    state["total_realized_usd"] = (
                        float(state.get("total_realized_usd") or 0.0) + profit_usd
                    )
                    state["total_trades"] = int(state.get("total_trades") or 0) + 1
                    if profit_usd >= 0:
                        state["wins"] = int(state.get("wins") or 0) + 1
                    else:
                        state["losses"] = int(state.get("losses") or 0) + 1
                    logger.info(
                        "CB EXIT Gate %s: %s (%s) $%.4f",
                        gate,
                        pid,
                        sell_reason,
                        profit_usd,
                    )
                    _log_trade(
                        {
                            "ts": now,
                            "type": "sell",
                            "engine": eng,
                            "reason": sell_reason,
                            "order_id": result.order_id,
                            "product_id": pid,
                            "entry_price": entry,
                            "exit_price": 0.0 if no_price else current,
                            "pnl_usd": profit_usd,
                        }
                    )
                    try:
                        bal_after = float(self._client.get_usd_balance())
                    except Exception:
                        bal_after = 0.0
                    log_trade(
                        platform="coinbase",
                        gate=gate,
                        product_id=pid,
                        side="sell",
                        strategy=str(pos.get("strategy") or ""),
                        entry_price=entry,
                        exit_price=0.0 if no_price else current,
                        size_usd=cost_usd,
                        pnl_usd=profit_usd,
                        exit_reason=_cb_supabase_exit_reason(sell_reason),
                        hold_seconds=max(0, int(age)),
                        balance_after=bal_after,
                        metadata={
                            "gate": pos.get("gate"),
                            "engine": pos.get("engine"),
                            "no_price": no_price,
                        },
                    )
                    try:
                        from trading_ai.shark.million_tracker import update_balance
                        from trading_ai.shark.trade_reports import record_trade

                        record_trade(
                            platform="coinbase",
                            gate=gate,
                            product_id=pid,
                            strategy=str(pos.get("strategy") or ""),
                            entry_price=entry,
                            exit_price=0.0 if no_price else current,
                            size_usd=cost_usd,
                            pnl_usd=profit_usd,
                            exit_reason=sell_reason,
                            hold_seconds=int(age),
                            balance_after=bal_after,
                        )
                        ka_bal = float(os.environ.get("KALSHI_ACTUAL_BALANCE", 0) or 0)
                        update_balance(bal_after, ka_bal)
                    except Exception:
                        pass
                    try:
                        from trading_ai.shark.reporting import send_telegram

                        profit_str = f"{pnl_pct * 100:+.2f}%"
                        emoji = "💰" if profit_usd > 0 else "🔴"
                        pk = float(upd.get("peak_price") or pos.get("peak_price") or 0.0)
                        tr = float(upd.get("trail_stop") or pos.get("trail_stop") or 0.0)
                        send_telegram(
                            f"{emoji} Gate {gate} EXIT: {pid}\n"
                            f"P&L: {profit_str} ${profit_usd:+.4f}\n"
                            f"Reason: {sell_reason}\n"
                            f"Hold: {int(age / 60)}min\n"
                            f"Peak was: ${pk:.4f}\n"
                            f"Trail was: ${tr:.4f}"
                        )
                    except Exception:
                        pass
                    state["positions"] = remaining + list(positions_snapshot[idx + 1 :])
                    save_coinbase_state(state)
                else:
                    if self._maybe_remove_phantom_after_failed_sell(state, pid, result):
                        pass
                    else:
                        logger.warning(
                            "CB SELL failed Gate %s %s (%s) — sell_pending=True",
                            gate,
                            pid,
                            getattr(result, "reason", None),
                        )
                        upd["sell_pending"] = True
                        remaining.append(upd)
                        state["positions"] = remaining + list(positions_snapshot[idx + 1 :])
                        save_coinbase_state(state)
                continue

            # ── Legacy engines (E1–E4) / old gate rows ─────────────────────
            tp, sl, tmin, trail = self._exit_params(eng)
            tlim = tmin * 60.0
            time_stop_seconds = int(round(tlim)) if tlim > 0 else 0
            logger.info(
                "Position %s age=%.0fs profit=%.3f%% (time_stop=%ds)",
                pid,
                age,
                pnl_pct * 100.0,
                time_stop_seconds,
            )
            peak_ref = current if current > 0 else float(pos.get("peak_price") or entry)
            peak = max(float(pos.get("peak_price") or entry), peak_ref)

            sell_reason = ""
            if sell_pending:
                sell_reason = "sell_pending_retry"
            elif no_price:
                sell_reason = "no_price_stop"
            elif tlim > 0:
                expiry_ts = float(
                    pos.get("expiry_time") or pos.get("must_sell_by") or 0.0
                )
                if expiry_ts > 0:
                    if now >= expiry_ts:
                        sell_reason = "timeout"
                else:
                    entry_tf = float(pos.get("entry_time") or 0.0)
                    if entry_tf <= 0:
                        entry_tf = now - 600.0
                    if now - entry_tf >= tlim:
                        sell_reason = "timeout"
            elif pnl_pct >= tp:
                sell_reason = f"tp +{tp*100:.2f}%"
            elif pnl_pct <= -sl:
                sell_reason = f"stop -{sl*100:.2f}%"
            elif (
                eng == 2
                and trail > 0
                and current > 0
                and current < peak * (1.0 - trail)
            ):
                sell_reason = f"trail -{trail*100:.3f}% peak"

            if not sell_reason:
                upd = dict(pos)
                upd["peak_price"] = peak
                hedge_tr = _env_float("COINBASE_HEDGE_TRIGGER_PCT", 0.001)
                if (
                    eng == 2
                    and pnl_pct >= hedge_tr
                    and not pos.get("ride2_tg")
                ):
                    upd["ride2_tg"] = True
                    try:
                        from trading_ai.shark.reporting import send_telegram

                        send_telegram(
                            f"🚀 E2 HEDGE/LOCK: {_cb_sym(pid)} now +{pnl_pct*100:.3f}% from entry "
                            f"(trigger {hedge_tr*100:.3f}%)"
                        )
                    except Exception:
                        pass
                remaining.append(upd)
                continue

            base_str = _fmt_base_size(pid, size_base)
            result = self._try_market_sell_twice(
                pid, base_str, size_base_from_pos=size_base
            )
            if no_price:
                profit_usd = -float(cost_usd)
            else:
                profit_usd = current * size_base - cost_usd

            if result is not None and result.success:
                exits += 1
                state["daily_pnl_usd"] = float(state.get("daily_pnl_usd") or 0.0) + profit_usd
                state["total_realized_usd"] = (
                    float(state.get("total_realized_usd") or 0.0) + profit_usd
                )
                state["total_trades"] = int(state.get("total_trades") or 0) + 1
                if profit_usd >= 0:
                    state["wins"] = int(state.get("wins") or 0) + 1
                else:
                    state["losses"] = int(state.get("losses") or 0) + 1
                if eng == 2:
                    state.setdefault("hunter_cooldown", {})[pid] = now

                xl = f"E{eng}"
                pnl_disp = "n/a" if no_price else f"{pnl_pct * 100.0:+.2f}%"
                logger.info(
                    "CB EXIT %s: %s %s $%.4f profit (%s)",
                    xl,
                    pid,
                    pnl_disp,
                    profit_usd,
                    sell_reason,
                )
                _log_trade(
                    {
                        "ts": now,
                        "type": "sell",
                        "engine": eng,
                        "reason": sell_reason,
                        "order_id": result.order_id,
                        "product_id": pid,
                        "entry_price": entry,
                        "exit_price": 0.0 if no_price else current,
                        "pnl_usd": profit_usd,
                    }
                )
                try:
                    bal_after = float(self._client.get_usd_balance())
                except Exception:
                    bal_after = 0.0
                log_trade(
                    platform="coinbase",
                    gate=str(pos.get("gate") or ""),
                    product_id=pid,
                    side="sell",
                    strategy=str(pos.get("strategy") or ""),
                    entry_price=entry,
                    exit_price=0.0 if no_price else current,
                    size_usd=cost_usd,
                    pnl_usd=profit_usd,
                    exit_reason=_cb_supabase_exit_reason(sell_reason),
                    hold_seconds=max(0, int(now - entry_t)),
                    balance_after=bal_after,
                    metadata={
                        "gate": pos.get("gate"),
                        "engine": pos.get("engine"),
                        "no_price": no_price,
                    },
                )
                try:
                    from trading_ai.shark.million_tracker import update_balance
                    from trading_ai.shark.trade_reports import record_trade

                    record_trade(
                        platform="coinbase",
                        gate=str(pos.get("gate") or ""),
                        product_id=pid,
                        strategy=str(pos.get("strategy") or ""),
                        entry_price=entry,
                        exit_price=0.0 if no_price else current,
                        size_usd=cost_usd,
                        pnl_usd=profit_usd,
                        exit_reason=sell_reason,
                        hold_seconds=int(now - entry_t),
                        balance_after=bal_after,
                    )
                    ka_bal = float(os.environ.get("KALSHI_ACTUAL_BALANCE", 0) or 0)
                    update_balance(bal_after, ka_bal)
                except Exception:
                    pass
                try:
                    from trading_ai.shark.reporting import send_telegram

                    sym = _cb_sym(pid)
                    if eng == 1:
                        if pnl_pct >= tp:
                            send_telegram(
                                f"💰 E1 SELL: {sym} +{pnl_pct*100:.1f}% profit ${abs(profit_usd):.3f}"
                            )
                        else:
                            send_telegram(
                                f"🛑 E1 STOP: {sym} {pnl_pct*100:.1f}% cut"
                            )
                    elif eng == 2:
                        if "trail" in sell_reason:
                            send_telegram(
                                f"🛑 E2 TRAIL: {sym} -{trail*100:.4f}% from peak sold"
                            )
                        else:
                            send_telegram(
                                f"💰 E2 WIN: {sym} +{pnl_pct*100:.4f}% profit ${abs(profit_usd):.2f}"
                            )
                    elif eng == 3:
                        alert = cost_usd * _env_float("COINBASE_ALERT_MIN_REALIZED_PCT", 0.01)
                        send_telegram(
                            f"💰 E3 PROFIT: {sym} +{pnl_pct*100:.2f}% ${abs(profit_usd):.2f} profit"
                            if profit_usd >= alert
                            else f"💰 E3: {sym} closed {pnl_pct*100:.2f}%"
                        )
                    elif eng == 4 and abs(profit_usd) >= cost_usd * _env_float(
                        "COINBASE_ALERT_MIN_REALIZED_PCT", 0.01
                    ):
                        send_telegram(
                            f"💰 E4 +{pnl_pct*100:.2f}% ${abs(profit_usd):.4f}"
                        )
                except Exception:
                    pass
                state["positions"] = remaining + list(positions_snapshot[idx + 1 :])
                save_coinbase_state(state)
            else:
                if self._maybe_remove_phantom_after_failed_sell(state, pid, result):
                    pass
                else:
                    logger.warning(
                        "CB SELL failed E%d %s (%s) — sell_pending=True",
                        eng,
                        pid,
                        getattr(result, "reason", None),
                    )
                    upd = dict(pos)
                    upd["sell_pending"] = True
                    upd["peak_price"] = peak
                    remaining.append(upd)
                    state["positions"] = remaining + list(positions_snapshot[idx + 1 :])
                    save_coinbase_state(state)

        state["positions"] = remaining
        save_coinbase_state(state)
        return exits
    def _order_size_e(
        self, engine: int, usd_balance: float, slots: int
    ) -> float:
        m = _min_order_usd(usd_balance)
        cap = _engine_quarter_cap(usd_balance)
        if slots <= 0:
            return m
        raw = cap / float(slots)
        if engine == 4:
            micro = max(
                m,
                _deployable_usd(usd_balance) * _env_float("COINBASE_E4_ORDER_PCT", 0.0125),
            )
            return max(m, min(raw, micro))
        return max(m, min(raw, cap))

    def _append_buy(
        self,
        state: Dict[str, Any],
        *,
        engine: int,
        product_id: str,
        mid: float,
        order_usd: float,
        now: float,
        order_id: str,
        gate: str = "",
        position_pct: float = 0.0,
        strategy: str = "",
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        pos: Dict[str, Any] = {
            "order_id": order_id,
            "product_id": product_id,
            "engine": engine,
            "entry_price": mid,
            "size_base": order_usd / mid,
            "cost_usd": order_usd,
            "entry_time": now,
            "peak_price": mid,
            "sell_pending": False,
            "exit_submitted": False,
            "exit_notified": False,
            "hedge_done": False,
        }
        if gate:
            pos["gate"] = gate
            pos["position_pct"] = position_pct
            if strategy:
                pos["strategy"] = strategy
        if extra_fields:
            pos.update(extra_fields)
        tlim = self._time_stop_limit_sec(pos)
        pos["expiry_time"] = now + tlim
        pos["must_sell_by"] = pos["expiry_time"]
        state.setdefault("positions", []).append(pos)
        label = f"Gate {gate}" if gate else f"E{engine}"
        logger.info(
            "CB BUY %s: %s $%.2f @ %.4f",
            label,
            product_id,
            order_usd,
            mid,
        )
        self._buys_this_scan += 1
        lt: Dict[str, Any] = {
            "ts": now,
            "type": "buy",
            "engine": engine,
            "order_id": order_id,
            "product_id": product_id,
            "entry_price": mid,
            "cost_usd": order_usd,
        }
        if gate:
            lt["gate"] = gate
        _log_trade(lt)
        save_coinbase_state(state)

    def _engine_1_dip(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        if not _env_bool("COINBASE_E1_ENABLED", False):
            return
        dip = _env_float("COINBASE_E1_DIP_PCT", 0.005)
        max_n = max(1, int(_env_float("COINBASE_E1_MAX_POSITIONS", 10.0)))
        slots = max_n
        order_usd = self._order_size_e(1, usd_balance, slots)
        bought = 0
        for pid, (bid, ask) in prices.items():
            if not str(pid).endswith("-USD") or bid <= 0 and ask <= 0:
                continue
            if _engine_open_count(state, 1) >= max_n:
                break
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref = _mid_at_or_before(hist, now - 300.0)
            if ref is None or ref <= 0:
                continue
            ch = (mid - ref) / ref
            if ch > -dip:
                continue
            ok, _ = _can_buy_engine(state, 1, pid, order_usd, usd_balance, max_n)
            if not ok:
                continue
            ok_liq, _why = self._buy_preflight_ok(pid, prices, now)
            if not ok_liq:
                continue
            if not self._rate_limiter.allow():
                break
            if not _mission_allows_coinbase_buy(pid, order_usd, usd_balance):
                continue
            r = self._client.place_market_buy(pid, order_usd)
            if not r.success:
                continue
            self._append_buy(
                state, engine=1, product_id=pid, mid=mid, order_usd=order_usd, now=now, order_id=r.order_id
            )
            bought += 1
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"🟢 E1 DIP: {_cb_sym(pid)} -{abs(ch)*100:.1f}% bought ${order_usd:.2f} @ ${mid:.4f}"
                )
            except Exception:
                pass
            if bought >= max_n:
                break

    def _engine_2_gainer(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        if not _env_bool("COINBASE_E2_ENABLED", False):
            return
        interval = _env_float("COINBASE_E2_SCAN_INTERVAL", 60.0)
        last = float(state.get("e2_scan_ts") or 0.0)
        if last > 0 and (now - last) < interval:
            return
        state["e2_scan_ts"] = now

        max_n = max(1, int(_env_float("COINBASE_E2_MAX_POSITIONS", 3.0)))
        order_usd = self._order_size_e(2, usd_balance, max_n)
        ranked = self._e2_candidates(state, prices, now)
        for row in ranked:
            if _engine_open_count(state, 2) >= max_n:
                break
            pid = row["product_id"]
            mid = float(row["mid"])
            hg = float(row["hour_pct"])
            ok, _ = _can_buy_engine(state, 2, pid, order_usd, usd_balance, max_n)
            if not ok:
                continue
            ok_liq, _why = self._buy_preflight_ok(pid, prices, now)
            if not ok_liq:
                continue
            if not self._rate_limiter.allow():
                break
            if not _mission_allows_coinbase_buy(pid, order_usd, usd_balance):
                continue
            r = self._client.place_market_buy(pid, order_usd)
            if not r.success:
                continue
            self._append_buy(
                state, engine=2, product_id=pid, mid=mid, order_usd=order_usd, now=now, order_id=r.order_id
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"🎯 E2 GAINER: {_cb_sym(pid)} +{hg*100:.1f}%/hr early mover ${order_usd:.2f} @ ${mid:.4f}"
                )
            except Exception:
                pass

    def _engine_3_scalp(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        if not _env_bool("COINBASE_E3_ENABLED", False):
            return
        trig = _env_float("COINBASE_E3_TRIG_PCT", 0.002)
        max_n = max(1, int(_env_float("COINBASE_E3_MAX_POSITIONS", 5.0)))
        order_usd = self._order_size_e(3, usd_balance, max_n)
        for pid in _E3_LIQUID:
            if pid not in prices:
                continue
            if _engine_open_count(state, 3) >= max_n:
                break
            bid, ask = prices[pid]
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref = _mid_at_or_before(hist, now - 120.0)
            if ref is None or ref <= 0:
                continue
            if (mid - ref) / ref < trig:
                continue
            ok, _ = _can_buy_engine(state, 3, pid, order_usd, usd_balance, max_n)
            if not ok:
                continue
            ok_liq, _why = self._buy_preflight_ok(pid, prices, now)
            if not ok_liq:
                continue
            if not self._rate_limiter.allow():
                break
            if not _mission_allows_coinbase_buy(pid, order_usd, usd_balance):
                continue
            r = self._client.place_market_buy(pid, order_usd)
            if not r.success:
                continue
            self._append_buy(
                state, engine=3, product_id=pid, mid=mid, order_usd=order_usd, now=now, order_id=r.order_id
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"⚡ E3 SCALP: {_cb_sym(pid)} +{trig*100:.1f}% momentum ${order_usd:.2f} @ ${mid:.2f}"
                )
            except Exception:
                pass

    def _engine_4_micro(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        if not _env_bool("COINBASE_E4_ENABLED", False):
            return
        trig = _env_float("COINBASE_E4_TRIG_PCT", 0.001)
        max_n = max(1, int(_env_float("COINBASE_E4_MAX_POSITIONS", 10.0)))
        order_usd = self._order_size_e(4, usd_balance, max_n)
        for pid in _E4_LIQUID:
            if pid not in prices:
                continue
            if _engine_open_count(state, 4) >= max_n:
                break
            bid, ask = prices[pid]
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref = _mid_at_or_before(hist, now - 60.0)
            if ref is None or ref <= 0:
                continue
            if (mid - ref) / ref < trig:
                continue
            ok, _ = _can_buy_engine(state, 4, pid, order_usd, usd_balance, max_n)
            if not ok:
                continue
            ok_liq, _why = self._buy_preflight_ok(pid, prices, now)
            if not ok_liq:
                continue
            if not self._rate_limiter.allow():
                break
            if not _mission_allows_coinbase_buy(pid, order_usd, usd_balance):
                continue
            r = self._client.place_market_buy(pid, order_usd)
            if not r.success:
                continue
            self._append_buy(
                state, engine=4, product_id=pid, mid=mid, order_usd=order_usd, now=now, order_id=r.order_id
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"⚡⚡ E4 MICRO: {_cb_sym(pid)} ${order_usd:.0f} @ ${mid:,.0f}"
                )
            except Exception:
                pass

    def _gate_a_scan(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> int:
        """Gate A: Night Gainer Hunter (10pm–8am ET)."""
        if not _coinbase_gates_mode():
            return 0
        if not _env_bool("COINBASE_GATE_A_ENABLED", True):
            return 0
        try:
            from zoneinfo import ZoneInfo

            now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now_et = datetime.now(timezone(timedelta(hours=-4)))
        if now_et.hour not in GATE_A_HOURS:
            return 0

        current_count = _count_gate(state, "A")
        if current_count >= GATE_A_MAX_POSITIONS:
            return 0

        bought = 0
        gainers: List[Dict[str, Any]] = []
        for pid, history in self._price_history.items():
            if not str(pid).endswith("-USD"):
                continue
            if len(history) < 60:
                continue
            current = float(history[-1][1])
            hour_ago = float(history[-60][1])
            if hour_ago <= 0 or current <= 0:
                continue
            if current < 0.01:
                continue
            hour_gain = (current - hour_ago) / hour_ago
            if hour_gain < GATE_A_MIN_HOUR_GAIN:
                continue
            recent_vol = (
                sum(
                    abs(float(history[-1][1]) - float(history[-i][1]))
                    for i in range(1, 11)
                )
                / 10.0
            )
            older_vol = (
                (
                    sum(
                        abs(float(history[-30][1]) - float(history[-30 - i][1]))
                        for i in range(1, 11)
                    )
                    / 10.0
                )
                if len(history) >= 40
                else 0.0
            )
            vol_ratio = recent_vol / older_vol if older_vol > 0 else 1.0
            if vol_ratio < GATE_A_MIN_VOL_RATIO:
                continue
            if _has_open_gate_product(state, "A", pid):
                continue
            gainers.append(
                {
                    "product_id": pid,
                    "hour_gain": hour_gain,
                    "vol_ratio": vol_ratio,
                    "current": current,
                }
            )

        gainers.sort(key=lambda x: -x["hour_gain"])

        for g in gainers:
            if current_count + bought >= GATE_A_MAX_POSITIONS:
                break
            pid = str(g["product_id"])
            bid, ask = prices.get(pid, (0.0, 0.0))
            if not bid:
                continue
            size_usd = min(usd_balance * GATE_A_ALLOCATION, 100.0)
            ok, _why = _can_buy_abc_gate(
                state, "A", pid, size_usd, usd_balance, GATE_A_MAX_POSITIONS
            )
            if not ok:
                continue
            if not self._buy_preflight_ok(pid, prices, now)[0]:
                continue
            if not _mission_allows_coinbase_buy(pid, size_usd, usd_balance):
                logger.info("MISSION BLOCK: coinbase Gate A %s", pid)
                continue
            if not self._rate_limiter.allow():
                break
            r = self._client.place_market_buy(pid, size_usd)
            if not r.success:
                continue
            bought += 1
            pos = {
                "product_id": pid,
                "gate": "A",
                "engine": 1,
                "strategy": "night_gainer",
                "entry_price": float(bid),
                "entry_time": now,
                "expiry_time": now + GATE_A_MAX_HOLD_MIN * 60.0,
                "cost_usd": size_usd,
                "size_base": size_usd / float(bid),
                "size_usd": size_usd,
                "peak_price": float(bid),
                "trail_stop": float(bid) * (1.0 - GATE_A_TRAIL_STOP),
                "exit_submitted": False,
                "exit_notified": False,
                "sell_pending": False,
                "order_id": r.order_id,
            }
            state.setdefault("positions", []).append(pos)
            save_coinbase_state(state)
            logger.info(
                "Gate A BUY: %s +%.1f%%/hr vol=%.1fx $%.2f @ $%.4f",
                pid,
                g["hour_gain"] * 100.0,
                g["vol_ratio"],
                size_usd,
                bid,
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"🌙 Gate A (night): {pid} +{g['hour_gain']*100:.1f}%/hr "
                    f"vol={g['vol_ratio']:.1f}x ${size_usd:.2f} @ ${bid:.4f}\n"
                    f"Target: +11% | Stop: -2.7% | Trail: -1.8%"
                )
            except Exception:
                pass

        return bought

    def _gate_b_scan(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> int:
        """Gate B: Day Momentum (9am–10pm ET)."""
        if not _coinbase_gates_mode():
            return 0
        if not _env_bool("COINBASE_GATE_B_ENABLED", True):
            return 0
        try:
            from zoneinfo import ZoneInfo

            now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now_et = datetime.now(timezone(timedelta(hours=-4)))
        if now_et.hour not in GATE_B_HOURS:
            return 0

        current_count = _count_gate(state, "B")
        if current_count >= GATE_B_MAX_POSITIONS:
            return 0

        bought = 0
        _ = GATE_B_RSI_CROSS
        for pid in GATE_B_PRODUCTS:
            if current_count + bought >= GATE_B_MAX_POSITIONS:
                break
            if _has_open_gate_product(state, "B", pid):
                continue
            signals = self._calculate_signals(pid, now)
            if signals.get("signal") != "buy":
                continue
            rsi = float(signals.get("rsi") or 50.0)
            macd = float(signals.get("macd") or 0.0)
            bullish = bool(signals.get("bullish_cross"))
            if rsi < 48.0 or rsi > 70.0:
                continue
            if not bullish:
                continue
            if macd <= 0.0:
                continue
            bid, ask = prices.get(pid, (0.0, 0.0))
            if not bid:
                continue
            size_usd = min(usd_balance * GATE_B_ALLOCATION, 100.0)
            ok, _why = _can_buy_abc_gate(
                state, "B", pid, size_usd, usd_balance, GATE_B_MAX_POSITIONS
            )
            if not ok:
                continue
            if not self._buy_preflight_ok(pid, prices, now)[0]:
                continue
            if not _mission_allows_coinbase_buy(pid, size_usd, usd_balance):
                logger.info("MISSION BLOCK: coinbase Gate B %s", pid)
                continue
            if not self._rate_limiter.allow():
                break
            r = self._client.place_market_buy(pid, size_usd)
            if not r.success:
                continue
            bought += 1
            pos = {
                "product_id": pid,
                "gate": "B",
                "engine": 1,
                "strategy": "day_momentum",
                "entry_price": float(bid),
                "entry_time": now,
                "expiry_time": now + GATE_B_MAX_HOLD_MIN * 60.0,
                "cost_usd": size_usd,
                "size_base": size_usd / float(bid),
                "size_usd": size_usd,
                "peak_price": float(bid),
                "trail_stop": float(bid) * (1.0 - GATE_B_TRAIL_STOP),
                "min_hold_until": now + GATE_B_MIN_HOLD_MIN * 60.0,
                "exit_submitted": False,
                "exit_notified": False,
                "sell_pending": False,
                "order_id": r.order_id,
            }
            state.setdefault("positions", []).append(pos)
            save_coinbase_state(state)
            logger.info(
                "Gate B BUY: %s RSI=%.1f MACD=%.6f $%.2f @ $%.4f",
                pid,
                rsi,
                macd,
                size_usd,
                bid,
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"☀️ Gate B (momentum): {pid} RSI={rsi:.0f} "
                    f"MACD={'↑' if macd > 0 else '↓'} ${size_usd:.2f} @ ${bid:.4f}\n"
                    f"Target: +3.3% | Stop: -1.3% | Trail: -1.3%"
                )
            except Exception:
                pass

        return bought

    def _gate_c_scan(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> int:
        """Gate C: BTC/ETH breakout (24/7)."""
        if not _coinbase_gates_mode():
            return 0
        if not _env_bool("COINBASE_GATE_C_ENABLED", True):
            return 0

        current_count = _count_gate(state, "C")
        if current_count >= GATE_C_MAX_POSITIONS:
            return 0

        bought = 0
        for pid in GATE_C_PRODUCTS:
            if current_count + bought >= GATE_C_MAX_POSITIONS:
                break
            if _has_open_gate_product(state, "C", pid):
                continue
            history = list(self._price_history.get(pid) or [])
            if len(history) < 30:
                continue
            current = float(history[-1][1])
            if current <= 0:
                continue
            recent_high = max(float(h[1]) for h in history[-30:])
            prev_high = (
                max(float(h[1]) for h in history[-60:-30]) if len(history) >= 60 else 0.0
            )
            breaking_out = (
                current >= recent_high * 0.999 and recent_high > prev_high * 1.002
            )
            if not breaking_out:
                continue
            recent_vol = (
                sum(
                    abs(float(history[-i][1]) - float(history[-i - 1][1]))
                    for i in range(1, 6)
                )
                / 5.0
            )
            older_vol = (
                sum(
                    abs(float(history[-10 - i][1]) - float(history[-10 - i - 1][1]))
                    for i in range(1, 6)
                )
                / 5.0
            )
            vol_ratio = recent_vol / older_vol if older_vol > 0 else 1.0
            if vol_ratio < GATE_C_VOL_SPIKE:
                continue
            signals = self._calculate_signals(pid, now)
            rsi = float(signals.get("rsi") or 50.0)
            if not (42.0 <= rsi <= 68.0):
                continue
            bid, ask = prices.get(pid, (0.0, 0.0))
            if not bid:
                continue
            size_usd = min(usd_balance * GATE_C_ALLOCATION, 200.0)
            ok, _why = _can_buy_abc_gate(
                state, "C", pid, size_usd, usd_balance, GATE_C_MAX_POSITIONS
            )
            if not ok:
                continue
            if not self._buy_preflight_ok(pid, prices, now)[0]:
                continue
            if not _mission_allows_coinbase_buy(pid, size_usd, usd_balance):
                logger.info("MISSION BLOCK: coinbase Gate C %s", pid)
                continue
            if not self._rate_limiter.allow():
                break
            r = self._client.place_market_buy(pid, size_usd)
            if not r.success:
                continue
            bought += 1
            pos = {
                "product_id": pid,
                "gate": "C",
                "engine": 1,
                "strategy": "btc_eth_breakout",
                "entry_price": float(bid),
                "entry_time": now,
                "expiry_time": now + GATE_C_MAX_HOLD_MIN * 60.0,
                "cost_usd": size_usd,
                "size_base": size_usd / float(bid),
                "size_usd": size_usd,
                "peak_price": float(bid),
                "trail_stop": float(bid) * (1.0 - GATE_C_TRAIL_STOP),
                "min_hold_until": now + GATE_C_MIN_HOLD_MIN * 60.0,
                "exit_submitted": False,
                "exit_notified": False,
                "sell_pending": False,
                "order_id": r.order_id,
            }
            state.setdefault("positions", []).append(pos)
            save_coinbase_state(state)
            logger.info(
                "Gate C BUY: %s breakout vol=%.1fx RSI=%.1f $%.2f @ $%.4f",
                pid,
                vol_ratio,
                rsi,
                size_usd,
                bid,
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"⚡ Gate C (breakout): {pid} vol={vol_ratio:.1f}x RSI={rsi:.0f} "
                    f"${size_usd:.2f} @ ${bid:.4f}\n"
                    f"Target: +2.2% | Stop: -1.1% | Trail: -1.1%"
                )
            except Exception:
                pass

        return bought

    def dawn_sweep_gate_a(self) -> int:
        """Cron 8am ET backup: same exit pass (dawn_sweep for Gate A is evaluated in ``_check_exits``)."""
        return self._run_exits_only()

    def _scan(self) -> Dict[str, Any]:
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)
        self._buys_this_scan = 0

        try:
            usd_balance = self._client.get_usd_balance()
        except Exception:
            usd_balance = 0.0

        now = time.time()
        buy_tick_sec = _env_float("COINBASE_BUY_SCAN_INTERVAL_SEC", 30.0)
        run_buy_tick = (now - self._last_ac_tick) >= buy_tick_sec
        if run_buy_tick:
            self._last_ac_tick = now

        scan_ids = _hf_scan_product_ids(self._client, state, now)
        pids = list(set(scan_ids) | set(collect_price_product_ids(state)))
        if _coinbase_gates_mode():
            extra = (
                set(GATE_B_PRODUCTS)
                | set(GATE_C_PRODUCTS)
                | set(_E4_LIQUID)
                | set(list(state.get("hf_product_cache") or [])[:400])
            )
            pids = list(set(pids) | extra)

        try:
            prices = self._client.get_prices_batched(pids)
        except Exception as exc:
            logger.warning("Coinbase price fetch failed: %s", exc)
            return {
                "ok": False,
                "error": "price_fetch",
                "trades_this_scan": 0,
                "exits_this_scan": 0,
                "exits": 0,
            }

        if not prices:
            return {
                "ok": False,
                "error": "no_prices",
                "trades_this_scan": 0,
                "exits_this_scan": 0,
                "exits": 0,
            }

        open_ids = collect_price_product_ids(state)
        missing = [x for x in open_ids if x and x not in prices]
        if missing:
            try:
                extra = self._client.get_prices_batched(missing)
                prices.update(extra)
            except Exception as exc:
                logger.warning("Coinbase price salvage: %s", exc)

        self._ingest_prices_into_history(prices, now)

        exits_this = 0
        if state.get("positions"):
            exits_this = self._check_exits(state, prices, now)

        if _coinbase_gates_mode():
            if run_buy_tick:
                if _env_bool("COINBASE_GATE_C_ENABLED", True):
                    self._gate_c_scan(state, prices, usd_balance, now)
                if _env_bool("COINBASE_GATE_A_ENABLED", True):
                    self._gate_a_scan(state, prices, usd_balance, now)
                if _env_bool("COINBASE_GATE_B_ENABLED", True):
                    self._gate_b_scan(state, prices, usd_balance, now)
        elif run_buy_tick:
            self._engine_4_micro(state, prices, usd_balance, now)
            self._engine_1_dip(state, prices, usd_balance, now)
            self._engine_2_gainer(state, prices, usd_balance, now)
            self._engine_3_scalp(state, prices, usd_balance, now)

        save_coinbase_state(state)
        return {
            "ok": True,
            "products": list(prices.keys()),
            "usd_balance": round(usd_balance, 2),
            "scan_ids": len(scan_ids),
            "trades_this_scan": int(self._buys_this_scan),
            "exits_this_scan": exits_this,
            "exits": exits_this,
        }


def sell_expired_positions_on_startup() -> int:
    """Sell any position past ``expiry_time`` or age-based time stop (restart-safe). Returns sell count."""
    if not coinbase_enabled():
        return 0
    acc = CoinbaseAccumulator()
    if not acc._client.has_credentials():
        return 0
    state = load_coinbase_state()
    positions = list(state.get("positions") or [])
    if not positions:
        return 0
    now = time.time()
    if acc._migrate_expiry_fields(state, now):
        save_coinbase_state(state)
    time_stop_fallback = _env_float("COINBASE_TIME_STOP_MIN", 3.0) * 60.0
    expired: List[Dict[str, Any]] = []
    fresh: List[Dict[str, Any]] = []
    for pos in positions:
        tlim = acc._time_stop_limit_sec(pos)
        ex = float(pos.get("expiry_time") or pos.get("must_sell_by") or 0.0)
        entry_t = float(pos.get("entry_time") or 0.0)
        is_expired = False
        if ex > 0 and now >= ex:
            is_expired = True
        elif entry_t > 0 and tlim > 0 and (now - entry_t) >= tlim:
            is_expired = True
        elif entry_t > 0 and time_stop_fallback > 0 and (now - entry_t) >= time_stop_fallback:
            is_expired = True
        if is_expired:
            expired.append(pos)
        else:
            fresh.append(pos)
    if not expired:
        return 0
    logger.info(
        "STARTUP: %d expired Coinbase position(s) — selling immediately",
        len(expired),
    )
    try:
        from trading_ai.shark.reporting import send_telegram

        send_telegram(
            f"🔄 Restart: selling {len(expired)} expired Coinbase position(s)"
        )
    except Exception:
        pass
    sold = 0
    still_open: List[Dict[str, Any]] = []
    for pos in expired:
        pid = str(pos.get("product_id") or "")
        size = float(pos.get("size_base") or 0.0)
        if not pid or size <= 0:
            still_open.append(pos)
            continue
        base_str = _fmt_base_size(pid, size)
        res = acc._try_market_sell_twice(pid, base_str, size_base_from_pos=size)
        if res is not None and res.success:
            sold += 1
            logger.info("STARTUP: sold expired %s", pid)
        elif acc._maybe_remove_phantom_after_failed_sell(state, pid, res):
            pass
        else:
            logger.warning("STARTUP: sell failed for expired %s — keeping in state", pid)
            still_open.append(pos)
    state["positions"] = fresh + still_open
    save_coinbase_state(state)
    logger.info("STARTUP: expired startup sells done (%d/%d)", sold, len(expired))
    return sold
