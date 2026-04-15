"""
Coinbase 24/7 — **high-frequency scalp** (Engines A / B / C) + optional **gainers** (D or hunter).

  **A — HF gainers**  Top hour movers when neither D nor gainer hunter is on.
  **B — Micro**    BTC-USD & ETH-USD: short-horizon momentum (pct of balance per trade).
  **C — Reversion**  Short dip vs 2m (pct of balance per trade).
  **D — Gainers**  ``COINBASE_GAINERS_ENABLED``: brokerage universe, top **60m** movers.
  **Gainer hunter**  ``COINBASE_GAINER_HUNTER_ENABLED``: Exchange ``/products`` + ``/stats``,
                     early movers (hour + last-30m), volume spike, below daily high.

Position sizing: ``COINBASE_HFT_POSITION_PCT``, ``COINBASE_GAINER_POSITION_PCT`` (see env),
``COINBASE_RESERVE_PCT``, ``COINBASE_MAX_DEPLOY_PCT`` — no fixed-dollar order envs.

Exits: ``_check_exits`` runs every scan after prices (before buy engines). Open products
without quotes get a salvage fetch. ``COINBASE_TIME_STOP_MIN`` (default 30, set 0 to
disable) forces market sell for any position age. ``COINBASE_PROFIT_TARGET_PCT`` /
``COINBASE_STOP_LOSS_PCT`` apply to engines A/B/C. State is saved after each buy/sell.

Prices: public tickers (Advanced Trade market + Exchange fallback — see ``outlets/coinbase.py``).
JWT only for ``/accounts`` and ``/orders``.

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


# Minimum notional (Coinbase floor); fixed-dollar envs removed — use pct sizing above this.
_ABSOLUTE_MIN_ORDER_USD = 1.0


def _reserve_pct() -> float:
    return _env_float("COINBASE_RESERVE_PCT", 0.20)


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
        "hunter_cooldown": {},
        "gh_vol_track": {},
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


def _telegram_early_mover_buy(
    sym: str, hour_pct: float, vol_mult: float, order_usd: float
) -> str:
    return (
        f"🎯 EARLY MOVER: {sym} +{hour_pct * 100:.1f}% and rising, "
        f"vol {vol_mult:.1f}x normal, buying ${order_usd:.2f}"
    )


def _telegram_riding(sym: str, gain_pct: float, peak: float) -> str:
    return f"🚀 RIDING: {sym} now +{gain_pct * 100:.1f}% from entry, peak=${peak:,.4f}"


def _telegram_hunter_sold(sym: str, profit_pct: float, pnl_usd: float, mins: float) -> str:
    return (
        f"💰 SOLD: {sym} +{profit_pct * 100:.1f}% profit ${abs(pnl_usd):.2f} in {mins:.0f}min"
    )


def _telegram_hunter_trail(sym: str) -> str:
    return f"🛑 TRAIL STOP: {sym} dropped 10% from peak, sold"


def _gainer_hunter_enabled() -> bool:
    return coinbase_enabled() and _env_bool("COINBASE_GAINER_HUNTER_ENABLED", False)


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
    max_n = max(1, int(_env_float("COINBASE_GAINERS_MAX_POSITIONS", 5.0)))
    max_deploy_pct = _env_float("COINBASE_GAINER_MAX_DEPLOY_PCT", 0.30)
    cap = max(0.0, usd_balance * max_deploy_pct)
    if _gainers_open_count(state) >= max_n:
        return False, f"gainers max positions ({max_n})"
    if cap > 0 and _gainers_deployed_usd(state) + order_usd > cap + 1e-6:
        return False, (
            f"gainer deploy cap {max_deploy_pct*100:.0f}% of balance (${cap:.2f}) would be exceeded"
        )
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
    min_order = _ABSOLUTE_MIN_ORDER_USD

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

    reserve = usd_balance * _reserve_pct()
    available = usd_balance - reserve
    if usd_amount > available:
        return False, (
            f"{_reserve_pct()*100:.0f}% cash reserve: need ${usd_amount:.2f} but "
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
        self._stats_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._buys_this_scan = 0

    def _get_available_usd(self, usd_balance: float) -> float:
        r = _reserve_pct()
        return max(0.0, float(usd_balance) * (1.0 - r))

    def _position_size_usd(self, pct: float, usd_balance: float) -> float:
        available = self._get_available_usd(usd_balance)
        size = available * pct
        cap = available * 0.20
        return max(_ABSOLUTE_MIN_ORDER_USD, min(size, cap))

    def _cached_exchange_stats(
        self, product_id: str, now: float
    ) -> Optional[Dict[str, Any]]:
        ttl = _env_float("COINBASE_GAINER_HUNTER_STATS_CACHE_SEC", 55.0)
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
        """Recent vs baseline rate of change of rolling 24h volume (proxy for activity spike)."""
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

    def _score_early_movers(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        now: float,
        *,
        apply_cooldown: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Rank USD pairs: hour momentum + last-30m acceleration, stats (below high), volume spike.
        """
        min_pct = _env_float("COINBASE_GAINER_HUNTER_MIN_PCT", 0.03)
        min_vol_usd = _env_float("COINBASE_GAINERS_MIN_VOLUME_USD", 10_000.0)
        min_vol_ratio = _env_float("COINBASE_GAINER_HUNTER_MIN_VOL_RATIO", 2.0)
        max_stats = max(20, int(_env_float("COINBASE_GAINER_HUNTER_MAX_STATS_FETCH", 80.0)))
        cooldown_sec = _env_float("COINBASE_GAINER_HUNTER_COOLDOWN_SEC", 1800.0)

        try:
            rows = self._client.list_brokerage_products()
        except Exception as exc:
            logger.warning("Coinbase hunter: brokerage products (volume) failed: %s", exc)
            rows = []
        vol_map = {
            str(r.get("product_id") or ""): _quote_volume_24h_usd(r)
            for r in rows
            if r.get("product_id")
        }

        cd = state.setdefault("hunter_cooldown", {})
        rough: List[Dict[str, Any]] = []
        for pid, (bid, ask) in prices.items():
            if not str(pid).endswith("-USD"):
                continue
            if bid <= 0 and ask <= 0:
                continue
            if apply_cooldown:
                lt = float(cd.get(pid) or 0.0)
                if lt > 0 and (now - lt) < cooldown_sec:
                    continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            p60 = _mid_at_or_before(hist, now - 3600.0)
            p30 = _mid_at_or_before(hist, now - 1800.0)
            if p60 is None or p30 is None or p60 <= 0 or p30 <= 0:
                continue
            hour_gr = (mid - p60) / p60
            if hour_gr < min_pct:
                continue
            first30 = (p30 - p60) / p60
            last30 = (mid - p30) / p30
            early_ok = last30 > 0 and last30 >= max(0.5 * hour_gr, 0.015) and (
                last30 >= first30 or first30 < 0.02
            )
            if not early_ok:
                continue
            v24 = float(vol_map.get(pid) or 0.0)
            if v24 < min_vol_usd:
                continue
            rough.append(
                {
                    "product_id": pid,
                    "mid": mid,
                    "hour_pct": hour_gr,
                    "last30_pct": last30,
                    "first30_pct": first30,
                    "quote_vol_24h": v24,
                }
            )

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
            if o > 0:
                pct_24 = (last - o) / o
                skip_24h = _env_float("COINBASE_GAINER_HUNTER_SKIP_24H_PCT", 0.50)
                if pct_24 >= skip_24h:
                    continue
            vr = self._gh_volume_ratio(state, pid, sv, now)
            if vr < 500.0 and vr < min_vol_ratio:
                continue
            out.append(
                {
                    **row,
                    "stats_last": last,
                    "stats_high": high,
                    "stats_open": o,
                    "vol_ratio": vr,
                    "pct_change_24h": ((last - o) / o * 100.0) if o > 0 else 0.0,
                }
            )
        out.sort(key=lambda x: (-x["hour_pct"], -x["vol_ratio"]))
        return out

    def _detect_early_movers(self) -> List[Dict[str, Any]]:
        """Public helper for tests / ops: refresh prices, score early movers (no trades)."""
        state = load_coinbase_state()
        now = time.time()
        scan_ids = _hf_scan_product_ids(self._client, state, now)
        pids = list(set(scan_ids) | set(collect_price_product_ids(state)))
        try:
            prices = self._client.get_prices_batched(pids)
        except Exception as exc:
            logger.warning("Coinbase _detect_early_movers prices: %s", exc)
            return []
        if not prices:
            return []
        self._ingest_prices_into_history(prices, now)
        return self._score_early_movers(state, prices, now, apply_cooldown=False)

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
        self._buys_this_scan = 0

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
            return {"ok": False, "error": "price_fetch", "trades_this_scan": 0}

        if not prices:
            return {"ok": False, "error": "no_prices", "trades_this_scan": 0}

        # Ensure every open position has a quote (otherwise _check_exits skips → stuck forever).
        open_ids = collect_price_product_ids(state)
        missing = [x for x in open_ids if x and x not in prices]
        if missing:
            try:
                extra = self._client.get_prices_batched(missing)
                prices.update(extra)
                if len(extra) < len(missing):
                    logger.warning(
                        "Coinbase: price salvage partial %d/%d open products quoted",
                        len(extra),
                        len(missing),
                    )
            except Exception as exc:
                logger.warning("Coinbase: price salvage for open positions failed: %s", exc)

        self._ingest_prices_into_history(prices, now)

        # Exit pass every tick before any buy engine (state is loaded from disk each scan).
        self._check_exits(state, prices, now)

        if run:
            if _gainer_hunter_enabled():
                self._engine_gainer_hunter(state, prices, usd_balance, now)
            elif _gainers_enabled():
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
            "trades_this_scan": int(self._buys_this_scan),
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
        self._buys_this_scan = int(getattr(self, "_buys_this_scan", 0)) + 1
        try:
            from trading_ai.shark.reporting import send_telegram

            send_telegram(_telegram_buy_line(_cb_sym(product_id), order_usd, mid))
        except Exception:
            pass
        save_coinbase_state(state)
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
        hft_pct = _env_float("COINBASE_HFT_POSITION_PCT", 0.05)

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
            order_usd = self._position_size_usd(hft_pct, usd_balance)
            self._place_buy(
                state,
                pid,
                mid,
                usd_balance,
                now,
                engine="B",
                order_usd=order_usd,
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
        hft_pct = _env_float("COINBASE_HFT_POSITION_PCT", 0.05)

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
            order_usd = self._position_size_usd(hft_pct, usd_balance)
            self._place_buy(
                state,
                pid,
                mid,
                usd_balance,
                now,
                engine="C",
                order_usd=order_usd,
                tp_pct=tp,
                sl_pct=sl,
                max_hold_sec=hold,
                label=f"reversion {ch*100:.2f}% vs 2m",
            )

    def _engine_gainer_hunter(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        """
        Early movers via Exchange ``/products/{id}/stats`` + internal 60m/30m price history.
        Strategy ``D`` with ``hunter`` flag for exit rules.
        """
        interval = _env_float("COINBASE_GAINERS_SCAN_INTERVAL", 60.0)
        last = float(state.get("gainers_scan_ts") or 0.0)
        if last > 0 and (now - last) < interval:
            return

        top_n = max(1, int(_env_float("COINBASE_GAINER_HUNTER_TOP_N", 3.0)))
        g_pct = _env_float("COINBASE_GAINER_POSITION_PCT", 0.10)

        ranked = self._score_early_movers(state, prices, now, apply_cooldown=True)
        state["gainers_scan_ts"] = now

        bought = 0
        for row in ranked:
            if bought >= top_n:
                break
            pid = row["product_id"]
            mid = float(row["mid"])
            hour_gr = float(row["hour_pct"])
            vr = float(row.get("vol_ratio") or 1.0)
            order_usd = self._position_size_usd(g_pct, usd_balance)
            ok, reason = _can_buy_gainers(state, pid, order_usd, usd_balance)
            if not ok:
                logger.debug("Coinbase hunter skip %s: %s", pid, reason)
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
                "hunter": True,
                "peak_price": mid,
                "momentum_1h_pct": hour_gr,
                "grid_level": None,
                "label": f"hunter +{hour_gr*100:.1f}% 60m vol {vr:.1f}x",
            }
            state.setdefault("positions", []).append(pos)
            usd_balance = max(0.0, usd_balance - order_usd)
            bought += 1
            self._buys_this_scan = int(getattr(self, "_buys_this_scan", 0)) + 1
            save_coinbase_state(state)
            _log_trade(
                {
                    "ts": now,
                    "type": "buy",
                    "strategy": "D",
                    "hunter": True,
                    "order_id": result.order_id,
                    "product_id": pid,
                    "entry_price": mid,
                    "cost_usd": order_usd,
                    "size_base": pos["size_base"],
                    "label": pos["label"],
                }
            )
            logger.info(
                "Coinbase hunter BUY: %s $%.2f @ $%.2f | +%.2f%% 60m",
                pid,
                order_usd,
                mid,
                hour_gr * 100,
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    _telegram_early_mover_buy(_cb_sym(pid), hour_gr, vr, order_usd)
                )
            except Exception:
                pass

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
        g_pct = _env_float("COINBASE_GAINER_POSITION_PCT", 0.10)

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
            order_usd = self._position_size_usd(g_pct, usd_balance)
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
                "hunter": False,
                "grid_level": None,
                "label": f"gainers +{hg*100:.1f}% {lookback/60:.0f}m",
            }
            state.setdefault("positions", []).append(pos)
            usd_balance = max(0.0, usd_balance - order_usd)
            bought += 1
            self._buys_this_scan = int(getattr(self, "_buys_this_scan", 0)) + 1
            save_coinbase_state(state)
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
        """Top hour gainers (>2%), trending, pct-sized buys, TP/SL/time per env."""
        min_h = _env_float("COINBASE_HF_A_MIN_HOUR_GAIN", 0.02)
        top_n = int(_env_float("COINBASE_HF_A_TOP_N", 13.0))
        tp = _env_float("COINBASE_HF_A_TP_PCT", 0.005)
        sl = _env_float("COINBASE_HF_A_SL_PCT", 0.003)
        hold = _env_float("COINBASE_HF_A_MAX_HOLD_SEC", 300.0)
        trend_sec = _env_float("COINBASE_HF_A_TREND_LOOKBACK_SEC", 300.0)
        cooldown = _env_float("COINBASE_HF_A_COOLDOWN_SEC", 300.0)
        hft_pct = _env_float("COINBASE_HFT_POSITION_PCT", 0.05)

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
            order_usd = self._position_size_usd(hft_pct, usd_balance)
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
            entry = float(pos.get("entry_price") or 0.0)
            size_base = float(pos.get("size_base") or 0.0)
            strategy = str(pos.get("hf_engine") or pos.get("strategy") or "A")

            if pid not in prices:
                logger.info(
                    "CB EXIT CHECK: %s entry=%.4f current=n/a pnl=n/a (no price in batch)",
                    pid,
                    entry,
                )
                remaining.append(pos)
                continue

            bid, _ask = prices[pid]
            current = bid
            cost_usd = float(pos.get("cost_usd") or 0.0)

            if entry <= 0 or size_base <= 0:
                logger.info(
                    "CB EXIT CHECK: %s entry=%.4f current=%.4f pnl=n/a (invalid size/entry)",
                    pid,
                    entry,
                    current,
                )
                remaining.append(pos)
                continue

            gain_pct = (current - entry) / entry
            entry_t = float(pos.get("entry_time") or 0.0)

            logger.info(
                "CB EXIT CHECK: %s entry=%.4f current=%.4f pnl=%.2f%%",
                pid,
                entry,
                current,
                gain_pct * 100.0,
            )

            profit_exit = False
            stop_exit = False
            trail_exit = False
            time_exit = False
            is_hunter = strategy == "D" and bool(pos.get("hunter"))

            ts_min = _env_float("COINBASE_TIME_STOP_MIN", 30.0)
            force_global_time = (
                ts_min > 0
                and entry_t > 0
                and (now - entry_t) >= ts_min * 60.0
            )

            if force_global_time:
                sell_reason = f"global_time_stop {ts_min:.0f}min"
            elif strategy == "D":
                peak = max(float(pos.get("peak_price") or entry), current)
                hist = list(self._price_history.get(pid) or [])

                if is_hunter:
                    take_pct = _env_float("COINBASE_GAINER_HUNTER_TAKE_PROFIT", 0.50)
                    trail = _env_float("COINBASE_GAINER_HUNTER_TRAILING_STOP", 0.10)
                    max_hold = _env_float("COINBASE_GAINER_HUNTER_MAX_HOLD_MIN", 120.0) * 60.0
                    flat_min = _env_float("COINBASE_GAINER_HUNTER_FLAT_MIN", 10.0) * 60.0
                    flat_th = _env_float("COINBASE_GAINER_HUNTER_FLAT_THRESH_PCT", 0.002)
                    vol_dry_max = _env_float("COINBASE_GAINER_HUNTER_VOL_DRY_RATIO", 0.50)
                    ride_at = _env_float("COINBASE_GAINER_HUNTER_RIDE_TELEGRAM_PCT", 0.05)

                    profit_exit = gain_pct >= take_pct
                    trail_exit = current < peak * (1.0 - trail)
                    time_exit = (now - entry_t) >= max_hold

                    ref_flat = _mid_at_or_before(hist, now - flat_min)
                    held_ok = (now - entry_t) >= flat_min
                    flat_exit = bool(
                        held_ok
                        and ref_flat
                        and ref_flat > 0
                        and abs(current - ref_flat) / ref_flat < flat_th
                    )

                    vol_dry_exit = False
                    st_now = self._cached_exchange_stats(pid, now)
                    if st_now:
                        sv = float(st_now.get("volume") or 0.0)
                        vr_now = self._gh_volume_ratio(state, pid, sv, now)
                        vol_dry_exit = vr_now < vol_dry_max and vr_now < 500.0

                    should_sell = (
                        profit_exit
                        or trail_exit
                        or time_exit
                        or flat_exit
                        or vol_dry_exit
                    )
                    if not should_sell:
                        upd = dict(pos)
                        upd["peak_price"] = peak
                        if gain_pct >= ride_at and not pos.get("ride_tg"):
                            upd["ride_tg"] = True
                            try:
                                from trading_ai.shark.reporting import send_telegram

                                send_telegram(
                                    _telegram_riding(_cb_sym(pid), gain_pct, peak)
                                )
                            except Exception:
                                pass
                        remaining.append(upd)
                        continue
                    if profit_exit:
                        sell_reason = f"hunter tp +{gain_pct*100:.2f}%"
                    elif trail_exit:
                        sell_reason = f"hunter trail −{trail*100:.0f}% from peak"
                    elif time_exit:
                        sell_reason = f"hunter time {((now - entry_t)/60):.1f}m"
                    elif flat_exit:
                        sell_reason = "hunter flat band"
                    else:
                        sell_reason = "hunter volume dry"
                else:
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
                tp = float(
                    pos.get("take_profit_pct")
                    or _env_float("COINBASE_PROFIT_TARGET_PCT", 0.001)
                )
                sl = float(
                    pos.get("stop_loss_pct")
                    or _env_float("COINBASE_STOP_LOSS_PCT", 0.005)
                )
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
                if is_hunter:
                    state.setdefault("hunter_cooldown", {})[pid] = now

                save_coinbase_state(state)

                _log_trade(
                    {
                        "ts": now,
                        "type": "sell",
                        "strategy": strategy,
                        "hunter": is_hunter,
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
                        if is_hunter:
                            mins = (now - entry_t) / 60.0
                            if trail_exit and not force_global_time:
                                send_telegram(_telegram_hunter_trail(sym))
                            else:
                                send_telegram(
                                    _telegram_hunter_sold(sym, gain_pct, pnl, mins)
                                )
                        elif trail_exit and not force_global_time:
                            tr = _env_float("COINBASE_GAINERS_TRAILING_STOP", 0.05)
                            send_telegram(_telegram_gainer_trailing_stop(sym, tr))
                        else:
                            send_telegram(
                                _telegram_gainer_sell_profit(sym, gain_pct, pnl)
                            )
                    elif stop_exit or (time_exit and pnl < 0) or (
                        sell_reason.startswith("global_time_stop")
                    ):
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
