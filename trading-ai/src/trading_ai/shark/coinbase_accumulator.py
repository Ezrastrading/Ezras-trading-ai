"""
Coinbase 24/7 — **high-frequency scalp** (Engines A / B / C) + optional **Engine D** (gainers).

  **A — HF gainers**  Top hour movers (when ``COINBASE_GAINERS_ENABLED`` is off). Skipped when D on.
  **B — Micro**    BTC-USD & ETH-USD only: short-horizon momentum / mean-reversion (env).
  **C — Reversion**  Short dip vs 2m (env).
  **D — Gainers**  ``COINBASE_GAINERS_ENABLED``: every ~60s evaluate all **Exchange** USD pairs
                   (``GET api.exchange.coinbase.com/products``), top **60m** % movers, optional
                   volume filter (default \$10k 24h when known); buy top N; exits per env.

Prices: public tickers (Advanced Trade market + Exchange fallback — see ``outlets/coinbase.py``).
JWT only for ``/accounts`` and ``/orders``.

Safety: ``COINBASE_MAX_PER_COIN_USD`` (default $6), ``COINBASE_MAX_POSITIONS`` (default 20),
``COINBASE_MAX_DEPLOY_PCT`` (0.80), 20% cash reserve, ``COINBASE_MAX_ORDERS_PER_MIN`` (high default),
``COINBASE_MIN_ORDER_USD``.

State: ``shark/state/coinbase_positions.json``; logs: ``shark/data/logs/coinbase_trade_log.jsonl``
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_data_dir, shark_state_path
from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.outlets.coinbase import CoinbaseAuthError, CoinbaseClient

load_shark_dotenv()
logger = logging.getLogger(__name__)

# ── env helpers ──────────────────────────────────────────────────────────────


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


def coinbase_enabled() -> bool:
    return _env_bool("COINBASE_ENABLED", False)


# ── state file paths ──────────────────────────────────────────────────────────


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
        "hf_product_cache": [],
        "hf_product_cache_ts": 0.0,
        "hf_last_buy_a": {},
        "gainers_scan_ts": 0.0,
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


# ── rate limiter ─────────────────────────────────────────────────────────────


class _RateLimiter:
    """Rolling 60s window for order API calls."""

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


def _telegram_buy_line(sym: str, order_usd: float, price: float) -> str:
    u = f"${order_usd:.0f}" if abs(order_usd - round(order_usd)) < 0.02 else f"${order_usd:.2f}"
    return f"🟢 CB BUY {sym} {u} @ ${price:,.0f}"


def _telegram_sell_profit_line(sym: str, gain_pct: float, pnl_usd: float) -> str:
    return f"💰 CB SELL {sym} +{gain_pct * 100:.1f}% +${abs(pnl_usd):.2f} profit"


def _telegram_stop_line(sym: str, gain_pct: float, pnl_usd: float) -> str:
    return f"🛑 CB STOP {sym} {gain_pct * 100:.1f}% -${abs(pnl_usd):.2f}"


def _telegram_gainer_buy(sym: str, momentum_pct: float, order_usd: float, price: float) -> str:
    u = f"${order_usd:.0f}" if abs(order_usd - round(order_usd)) < 0.02 else f"${order_usd:.2f}"
    return f"🚀 GAINER BUY: {sym} +{momentum_pct * 100:.1f}% momentum, {u} @ ${price:,.4f}"


def _telegram_gainer_sell_profit(sym: str, profit_pct: float, pnl_usd: float) -> str:
    return f"💰 GAINER SELL: {sym} +{profit_pct * 100:.1f}% profit, ${abs(pnl_usd):.2f}"


def _telegram_gainer_trailing_stop(sym: str, trail_pct: float) -> str:
    return f"🛑 GAINER STOP: {sym} trailing stop -{trail_pct * 100:.0f}% from peak"


def _gainers_enabled() -> bool:
    return coinbase_enabled() and _env_bool("COINBASE_GAINERS_ENABLED", False)


def _gainers_deployed_usd(state: Dict[str, Any]) -> float:
    return sum(
        float(p.get("cost_usd") or 0.0)
        for p in (state.get("positions") or [])
        if str(p.get("strategy") or "") == "D"
    )


def _gainers_open_count(state: Dict[str, Any]) -> int:
    return sum(
        1 for p in (state.get("positions") or []) if str(p.get("strategy") or "") == "D"
    )


def _can_buy_gainers(
    state: Dict[str, Any],
    product_id: str,
    order_usd: float,
    usd_balance: float,
) -> Tuple[bool, str]:
    budget = _env_float("COINBASE_GAINERS_BUDGET", 15.0)
    max_n = max(1, int(_env_float("COINBASE_GAINERS_MAX_POSITIONS", 5.0)))
    if _gainers_open_count(state) >= max_n:
        return False, f"gainers max positions ({max_n})"
    if _gainers_deployed_usd(state) + order_usd > budget + 1e-6:
        return False, f"gainers budget ${budget:.0f} would be exceeded"
    has_d = any(
        p.get("product_id") == product_id and str(p.get("strategy") or "") == "D"
        for p in (state.get("positions") or [])
    )
    if has_d:
        return False, "already have gainers position on product"
    return _can_buy(state, product_id, order_usd, usd_balance)


def _mid_at_or_before(
    hist: List[Tuple[float, float]], ts: float
) -> Optional[float]:
    old = [(t, p) for t, p in hist if t <= ts]
    if not old:
        return None
    return float(old[-1][1])


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


# ── sizing & safety ───────────────────────────────────────────────────────────


def _total_open_cost(state: Dict[str, Any]) -> float:
    return sum(float(p.get("cost_usd") or 0.0) for p in state.get("positions") or [])


def _product_open_cost(state: Dict[str, Any], product_id: str) -> float:
    return sum(
        float(p.get("cost_usd") or 0.0)
        for p in (state.get("positions") or [])
        if p.get("product_id") == product_id
    )


def _has_open_position(state: Dict[str, Any], product_id: str) -> bool:
    return any(
        str(p.get("product_id")) == product_id for p in (state.get("positions") or [])
    )


def _max_per_coin_usd() -> float:
    return _env_float("COINBASE_MAX_PER_COIN_USD", 6.0)


def _can_buy(
    state: Dict[str, Any], product_id: str, usd_amount: float, usd_balance: float
) -> Tuple[bool, str]:
    max_total = _env_float("COINBASE_MAX_TOTAL_USD", 2000.0)
    max_daily_loss = _env_float("COINBASE_MAX_DAILY_LOSS", 200.0)
    raw_max = os.environ.get("COINBASE_MAX_POSITIONS")
    if raw_max is not None and str(raw_max).strip() != "":
        max_open = int(float(raw_max))
    else:
        max_open = int(_env_float("COINBASE_MAX_OPEN_POSITIONS", 20.0))
    min_order = _env_float("COINBASE_MIN_ORDER_USD", 2.0)

    daily_loss = -(float(state.get("daily_pnl_usd") or 0.0))
    if daily_loss >= max_daily_loss:
        return False, f"daily loss limit ${max_daily_loss:.0f} hit (${daily_loss:.2f} today)"

    if usd_amount < min_order:
        return False, f"order ${usd_amount:.2f} below minimum ${min_order:.2f}"

    open_count = len(state.get("positions") or [])
    if open_count >= max_open:
        return False, f"max open positions ({max_open}) reached"

    current_exposure = _total_open_cost(state)
    if current_exposure + usd_amount > max_total:
        return False, f"would exceed COINBASE_MAX_TOTAL_USD ${max_total:.0f}"

    max_deploy_pct = _env_float("COINBASE_MAX_DEPLOY_PCT", 0.80)
    if usd_balance > 0 and current_exposure + usd_amount > usd_balance * max_deploy_pct:
        return False, (
            f"max deploy {max_deploy_pct*100:.0f}% of ${usd_balance:.2f} balance would be exceeded"
        )

    cap = _max_per_coin_usd()
    product_cost = _product_open_cost(state, product_id)
    if cap > 0 and product_cost + usd_amount > cap:
        return False, (
            f"{product_id} cap ${cap:.0f} would be exceeded "
            f"(${product_cost:.2f} already open)"
        )

    reserve = usd_balance * 0.20
    available = usd_balance - reserve
    if usd_amount > available:
        return False, (
            f"20% cash reserve: need ${usd_amount:.2f} but "
            f"only ${available:.2f} deployable (balance ${usd_balance:.2f})"
        )

    return True, "ok"


def _fmt_base_size(product_id: str, size: float) -> str:
    if product_id.startswith("BTC"):
        return f"{size:.8f}"
    if product_id.startswith("ETH"):
        return f"{size:.6f}"
    return f"{size:.6f}"


def _parse_csv_products(raw: Optional[str]) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def _hf_scan_product_ids(client: CoinbaseClient, state: Dict[str, Any], now: float) -> List[str]:
    """
    Product ids to poll every tick: **all online USD pairs** from Coinbase Exchange
    ``GET /products`` (cached), unless ``COINBASE_HF_SCAN_PRODUCTS`` is set (CSV override).
    """
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
    """Open positions only (full universe comes from :func:`_hf_scan_product_ids`)."""
    ids: List[str] = []
    for p in state.get("positions") or []:
        pid = p.get("product_id")
        if pid:
            ids.append(str(pid))
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# CoinbaseAccumulator
# ─────────────────────────────────────────────────────────────────────────────


class CoinbaseAccumulator:
    """
    HF scalp engines (A/B/C). One scan per scheduler tick (default 60s).
    """

    def __init__(self, client: Optional[CoinbaseClient] = None) -> None:
        self._client = client or CoinbaseClient()
        self._rate_limiter = _RateLimiter()
        self._price_history: Dict[str, deque[Tuple[float, float]]] = {}
        self._last_ac_tick: float = -1e9

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
            "by_product": by_product,
        }

    def load_and_check_positions_on_startup(self) -> None:
        if not coinbase_enabled():
            return
        if not self._client.has_credentials():
            return
        logger.info("Coinbase HF: startup exit check ...")
        try:
            state = load_coinbase_state()
            _reset_daily_pnl_if_needed(state)
            now = time.time()
            scan_ids = _hf_scan_product_ids(self._client, state, now)
            pids = list(set(scan_ids) | set(collect_price_product_ids(state)))
            prices = self._client.get_prices_batched(pids)
            if not prices:
                logger.warning("Coinbase startup: no prices")
                return
            self._ingest_prices_into_history(prices, now)
            self._check_exits(state, prices, now)
            save_coinbase_state(state)
            logger.info(
                "Coinbase startup: %d positions after exit scan",
                len(state.get("positions") or []),
            )
        except Exception as exc:
            logger.warning("Coinbase startup check failed (non-fatal): %s", exc)

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

    def _scan(self) -> Dict[str, Any]:
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)

        try:
            usd_balance = self._client.get_usd_balance()
        except Exception:
            usd_balance = 0.0

        now = time.time()
        run = (now - self._last_ac_tick) >= 60.0
        if run:
            self._last_ac_tick = now

        scan_ids = _hf_scan_product_ids(self._client, state, now)
        pids = list(set(scan_ids) | set(collect_price_product_ids(state)))

        try:
            prices = self._client.get_prices_batched(pids)
        except Exception as exc:
            logger.warning("Coinbase price fetch failed: %s", exc)
            return {"ok": False, "error": "price_fetch"}

        if not prices:
            return {"ok": False, "error": "no_prices"}

        self._ingest_prices_into_history(prices, now)

        self._check_exits(state, prices, now)

        if run:
            if _gainers_enabled():
                self._engine_d_gainers(state, prices, usd_balance, now)
            else:
                self._engine_a_gainers(state, prices, usd_balance, now)
            self._engine_b_micro(state, prices, usd_balance, now)
            self._engine_c_mean_reversion(state, prices, usd_balance, now)

        save_coinbase_state(state)
        return {
            "ok": True,
            "products": list(prices.keys()),
            "usd_balance": round(usd_balance, 2),
            "scan_ids": len(scan_ids),
        }

    def _place_buy(
        self,
        state: Dict[str, Any],
        product_id: str,
        mid: float,
        usd_balance: float,
        now: float,
        *,
        engine: str,
        order_usd: float,
        tp_pct: float,
        sl_pct: float,
        max_hold_sec: float,
        label: str,
    ) -> bool:
        if _has_open_position(state, product_id):
            return False
        ok, reason = _can_buy(state, product_id, order_usd, usd_balance)
        if not ok:
            logger.debug("Coinbase %s blocked (%s): %s", engine, product_id, reason)
            return False
        if not self._rate_limiter.allow():
            return False
        result = self._client.place_market_buy(product_id, order_usd)
        if not result.success:
            return False
        pos: Dict[str, Any] = {
            "order_id": result.order_id,
            "product_id": product_id,
            "entry_price": mid,
            "size_base": order_usd / mid,
            "cost_usd": order_usd,
            "entry_time": now,
            "strategy": engine,
            "hf_engine": engine,
            "take_profit_pct": tp_pct,
            "stop_loss_pct": sl_pct,
            "max_hold_sec": max_hold_sec,
            "grid_level": None,
            "label": label,
        }
        state.setdefault("positions", []).append(pos)
        _log_trade(
            {
                "ts": now,
                "type": "buy",
                "strategy": engine,
                "order_id": result.order_id,
                "product_id": product_id,
                "entry_price": mid,
                "cost_usd": order_usd,
                "size_base": pos["size_base"],
                "label": label,
            }
        )
        logger.info(
            "Coinbase %s BUY: %s $%.2f @ $%.2f | %s",
            engine,
            product_id,
            order_usd,
            mid,
            label,
        )
        try:
            from trading_ai.shark.reporting import send_telegram

            send_telegram(_telegram_buy_line(_cb_sym(product_id), order_usd, mid))
        except Exception:
            pass
        return True

    def _engine_b_micro(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        """BTC/ETH: +0.1% vs 2m → buy, TP +0.3%, SL −0.2%, hold ≤3m."""
        tp = _env_float("COINBASE_HF_B_TP_PCT", 0.003)
        sl = _env_float("COINBASE_HF_B_SL_PCT", 0.002)
        hold = _env_float("COINBASE_HF_B_MAX_HOLD_SEC", 180.0)
        trig = _env_float("COINBASE_HF_B_TRIG_PCT", 0.001)
        order_usd = _env_float("COINBASE_HF_B_ORDER_USD", 3.0)

        for pid in ("BTC-USD", "ETH-USD"):
            if pid not in prices:
                continue
            bid, ask = prices[pid]
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref = _mid_at_or_before(hist, now - 120.0)
            if ref is None or ref <= 0:
                continue
            ch = (mid - ref) / ref
            if ch < trig:
                continue
            self._place_buy(
                state,
                pid,
                mid,
                usd_balance,
                now,
                engine="B",
                order_usd=max(_env_float("COINBASE_MIN_ORDER_USD", 2.0), order_usd),
                tp_pct=tp,
                sl_pct=sl,
                max_hold_sec=hold,
                label=f"micro +{ch*100:.2f}% vs 2m",
            )

    def _engine_c_mean_reversion(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        """Drop ≥0.2% in 2m → buy, TP +0.3%, SL −0.5%, hold ≤5m."""
        tp = _env_float("COINBASE_HF_C_TP_PCT", 0.003)
        sl = _env_float("COINBASE_HF_C_SL_PCT", 0.005)
        hold = _env_float("COINBASE_HF_C_MAX_HOLD_SEC", 300.0)
        drop = _env_float("COINBASE_HF_C_DROP_PCT", 0.002)
        order_usd = _env_float("COINBASE_HF_C_ORDER_USD", 2.0)

        for pid in list(prices.keys()):
            if pid in ("BTC-USD", "ETH-USD"):
                continue
            if pid not in prices:
                continue
            bid, ask = prices[pid]
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref = _mid_at_or_before(hist, now - 120.0)
            if ref is None or ref <= 0:
                continue
            ch = (mid - ref) / ref
            if ch > -drop:
                continue
            self._place_buy(
                state,
                pid,
                mid,
                usd_balance,
                now,
                engine="C",
                order_usd=max(_env_float("COINBASE_MIN_ORDER_USD", 2.0), order_usd),
                tp_pct=tp,
                sl_pct=sl,
                max_hold_sec=hold,
                label=f"reversion {ch*100:.2f}% vs 2m",
            )

    def _engine_d_gainers(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        """
        Top **60m** % gainers (configurable lookback), optional 24h volume filter, 5m uptrend.
        Buys use strategy ``D`` (exits: +3%, trail 5% from peak, time stop).
        """
        interval = _env_float("COINBASE_GAINERS_SCAN_INTERVAL", 60.0)
        last = float(state.get("gainers_scan_ts") or 0.0)
        if last > 0 and (now - last) < interval:
            return

        lookback = _env_float("COINBASE_GAINERS_LOOKBACK_SEC", 3600.0)
        min_pct = _env_float("COINBASE_GAINERS_MIN_PCT", 0.05)
        min_vol = _env_float("COINBASE_GAINERS_MIN_VOLUME_USD", 10_000.0)
        strict_vol = _env_bool("COINBASE_GAINERS_STRICT_VOLUME", False)
        top_n = max(1, int(_env_float("COINBASE_GAINERS_TOP_N", 5.0)))
        trend_sec = _env_float("COINBASE_GAINERS_TREND_LOOKBACK_SEC", 300.0)
        order_usd = _env_float("COINBASE_GAINERS_ORDER_USD", 5.0)
        lo = _env_float("COINBASE_GAINERS_ORDER_MIN_USD", 3.0)
        hi = _env_float("COINBASE_GAINERS_ORDER_MAX_USD", 5.0)
        order_usd = min(hi, max(lo, order_usd))
        min_order = _env_float("COINBASE_MIN_ORDER_USD", 2.0)
        order_usd = max(min_order, order_usd)

        try:
            rows = self._client.list_brokerage_products()
        except Exception as exc:
            logger.warning("Coinbase D: list products failed: %s", exc)
            return

        vol_map = {
            str(r.get("product_id") or ""): _quote_volume_24h_usd(r)
            for r in rows
            if r.get("product_id")
        }

        scored: List[Tuple[str, float, float]] = []
        for pid, (bid, ask) in prices.items():
            if bid <= 0 and ask <= 0:
                continue
            v24 = vol_map.get(pid)
            if v24 is not None and v24 < min_vol:
                continue
            if v24 is None and strict_vol:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref_h = _mid_at_or_before(hist, now - lookback)
            if ref_h is None or ref_h <= 0:
                continue
            hg = (mid - ref_h) / ref_h
            if hg < min_pct:
                continue
            ref_tr = _mid_at_or_before(hist, now - trend_sec)
            if ref_tr is not None and mid <= float(ref_tr):
                continue
            scored.append((pid, hg, mid))

        scored.sort(key=lambda x: -x[1])
        state["gainers_scan_ts"] = now

        bought = 0
        for pid, hg, mid in scored:
            if bought >= top_n:
                break
            ok, reason = _can_buy_gainers(state, pid, order_usd, usd_balance)
            if not ok:
                logger.debug("Coinbase D skip %s: %s", pid, reason)
                continue
            if not self._rate_limiter.allow():
                break
            result = self._client.place_market_buy(pid, order_usd)
            if not result.success:
                continue
            pos: Dict[str, Any] = {
                "order_id": result.order_id,
                "product_id": pid,
                "entry_price": mid,
                "size_base": order_usd / mid,
                "cost_usd": order_usd,
                "entry_time": now,
                "strategy": "D",
                "hf_engine": "D",
                "peak_price": mid,
                "momentum_1h_pct": hg,
                "grid_level": None,
                "label": f"gainers +{hg*100:.1f}% {lookback/60:.0f}m",
            }
            state.setdefault("positions", []).append(pos)
            usd_balance = max(0.0, usd_balance - order_usd)
            bought += 1
            _log_trade(
                {
                    "ts": now,
                    "type": "buy",
                    "strategy": "D",
                    "order_id": result.order_id,
                    "product_id": pid,
                    "entry_price": mid,
                    "cost_usd": order_usd,
                    "size_base": pos["size_base"],
                    "label": pos["label"],
                }
            )
            logger.info(
                "Coinbase D BUY: %s $%.2f @ $%.2f | +%.2f%% vs %.0fm",
                pid,
                order_usd,
                mid,
                hg * 100,
                lookback / 60.0,
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    _telegram_gainer_buy(_cb_sym(pid), hg, order_usd, mid)
                )
            except Exception:
                pass

    def _engine_a_gainers(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        """Top hour gainers (>2%), trending, buy $2–3, TP/SL/time per env."""
        min_h = _env_float("COINBASE_HF_A_MIN_HOUR_GAIN", 0.02)
        top_n = int(_env_float("COINBASE_HF_A_TOP_N", 13.0))
        tp = _env_float("COINBASE_HF_A_TP_PCT", 0.005)
        sl = _env_float("COINBASE_HF_A_SL_PCT", 0.003)
        hold = _env_float("COINBASE_HF_A_MAX_HOLD_SEC", 300.0)
        trend_sec = _env_float("COINBASE_HF_A_TREND_LOOKBACK_SEC", 300.0)
        cooldown = _env_float("COINBASE_HF_A_COOLDOWN_SEC", 300.0)
        lo = _env_float("COINBASE_HF_A_ORDER_MIN_USD", 2.0)
        hi = _env_float("COINBASE_HF_A_ORDER_MAX_USD", 3.0)

        scored: List[Tuple[str, float, float]] = []
        for pid, (bid, ask) in prices.items():
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref_h = _mid_at_or_before(hist, now - 3600.0)
            if ref_h is None or ref_h <= 0:
                continue
            hg = (mid - ref_h) / ref_h
            if hg < min_h:
                continue
            ref_tr = _mid_at_or_before(hist, now - trend_sec)
            if ref_tr is not None and mid <= float(ref_tr):
                continue
            scored.append((pid, hg, mid))

        scored.sort(key=lambda x: -x[1])
        last_a = state.setdefault("hf_last_buy_a", {})

        for pid, _hg, mid in scored[:top_n]:
            if _has_open_position(state, pid):
                continue
            lt = float(last_a.get(pid) or 0.0)
            if now - lt < cooldown:
                continue
            order_usd = min(hi, max(lo, (lo + hi) / 2.0))
            order_usd = max(_env_float("COINBASE_MIN_ORDER_USD", 2.0), order_usd)
            if self._place_buy(
                state,
                pid,
                mid,
                usd_balance,
                now,
                engine="A",
                order_usd=order_usd,
                tp_pct=tp,
                sl_pct=sl,
                max_hold_sec=hold,
                label="gainers scalp",
            ):
                last_a[pid] = now
                state["hf_last_buy_a"] = last_a

    def _check_exits(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        now: float,
    ) -> None:
        remaining: List[Dict[str, Any]] = []
        for pos in list(state.get("positions") or []):
            pid = str(pos.get("product_id") or "")
            if pid not in prices:
                remaining.append(pos)
                continue

            bid, _ask = prices[pid]
            current = bid
            entry = float(pos.get("entry_price") or 0.0)
            size_base = float(pos.get("size_base") or 0.0)
            cost_usd = float(pos.get("cost_usd") or 0.0)
            strategy = str(pos.get("hf_engine") or pos.get("strategy") or "A")

            if entry <= 0 or size_base <= 0:
                remaining.append(pos)
                continue

            gain_pct = (current - entry) / entry
            entry_t = float(pos.get("entry_time") or 0.0)

            profit_exit = False
            stop_exit = False
            trail_exit = False
            time_exit = False

            if strategy == "D":
                peak = max(float(pos.get("peak_price") or entry), current)
                take_pct = _env_float("COINBASE_GAINERS_PROFIT_PCT", 0.03)
                trail = _env_float("COINBASE_GAINERS_TRAILING_STOP", 0.05)
                tstop_sec = _env_float("COINBASE_GAINERS_TIME_STOP_MIN", 30.0) * 60.0
                profit_exit = gain_pct >= take_pct
                trail_exit = current < peak * (1.0 - trail)
                time_exit = (now - entry_t) >= tstop_sec
                should_sell = profit_exit or trail_exit or time_exit
                if not should_sell:
                    upd = dict(pos)
                    upd["peak_price"] = peak
                    remaining.append(upd)
                    continue
                if profit_exit:
                    sell_reason = f"gainer tp +{gain_pct*100:.2f}%"
                elif trail_exit:
                    sell_reason = f"gainer trail −{trail*100:.0f}% from peak"
                else:
                    sell_reason = f"gainer time {((now - entry_t)/60):.1f}m"
            else:
                tp = float(pos.get("take_profit_pct") or _env_float("COINBASE_PROFIT_TARGET_PCT", 0.02))
                sl = float(pos.get("stop_loss_pct") or _env_float("COINBASE_STOP_LOSS_PCT", 0.01))
                max_hold = float(pos.get("max_hold_sec") or 999999.0)
                time_exit = (now - entry_t) >= max_hold
                profit_exit = gain_pct >= tp
                stop_exit = gain_pct <= -sl
                should_sell = time_exit or profit_exit or stop_exit
                if not should_sell:
                    remaining.append(pos)
                    continue
                if time_exit:
                    sell_reason = f"time_stop {((now - entry_t)/60):.1f}m"
                elif profit_exit:
                    sell_reason = f"tp +{gain_pct*100:.2f}%"
                else:
                    sell_reason = f"sl {gain_pct*100:.2f}%"

            if not self._rate_limiter.allow():
                remaining.append(pos)
                continue

            base_str = _fmt_base_size(pid, size_base)
            result = self._client.place_market_sell(pid, base_str)

            if result.success:
                proceeds = current * size_base
                pnl = proceeds - cost_usd
                state["daily_pnl_usd"] = float(state.get("daily_pnl_usd") or 0.0) + pnl
                state["total_realized_usd"] = (
                    float(state.get("total_realized_usd") or 0.0) + pnl
                )

                _log_trade(
                    {
                        "ts": now,
                        "type": "sell",
                        "strategy": strategy,
                        "reason": sell_reason,
                        "order_id": result.order_id,
                        "product_id": pid,
                        "entry_price": entry,
                        "exit_price": current,
                        "size_base": size_base,
                        "cost_usd": cost_usd,
                        "proceeds_usd": proceeds,
                        "pnl_usd": pnl,
                        "gain_pct": gain_pct,
                    }
                )
                logger.info(
                    "Coinbase %s SELL: %s @ $%.4f | PnL $%.4f (%s)",
                    strategy,
                    pid,
                    current,
                    pnl,
                    sell_reason,
                )
                try:
                    from trading_ai.shark.reporting import send_telegram

                    sym = _cb_sym(pid)
                    if strategy == "D":
                        if trail_exit:
                            tr = _env_float("COINBASE_GAINERS_TRAILING_STOP", 0.05)
                            send_telegram(_telegram_gainer_trailing_stop(sym, tr))
                        else:
                            send_telegram(
                                _telegram_gainer_sell_profit(sym, gain_pct, pnl)
                            )
                    elif stop_exit or (time_exit and pnl < 0):
                        send_telegram(_telegram_stop_line(sym, gain_pct, pnl))
                    else:
                        send_telegram(_telegram_sell_profit_line(sym, gain_pct, pnl))
                except Exception:
                    pass
            else:
                logger.warning(
                    "Coinbase SELL failed (%s): %s — keeping position", pid, result.reason
                )
                remaining.append(pos)
                continue

        state["positions"] = remaining
