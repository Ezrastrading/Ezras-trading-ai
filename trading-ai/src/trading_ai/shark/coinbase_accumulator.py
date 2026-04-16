"""
Coinbase — **four engines** (E1–E4), **25% / 25% / 25% / 25%** of deployable capital each,
or **Gate A + Gate B** when ``COINBASE_GATES_MODE=true`` (percent-sized positions;
defaults ``COINBASE_GATE_A_POSITIONS`` / ``COINBASE_GATE_B_POSITIONS``).

Deployable = ``balance * COINBASE_MAX_DEPLOY_PCT`` (default 0.80, i.e. 20% reserve via
``COINBASE_RESERVE_PCT``). Each engine may deploy up to **25%** of that deployable pool.

**E1 — Dip buyer**  All USD pairs: 5m dip → buy; TP / SL / time via ``COINBASE_E1_*``.
**E2 — Gainer hunter**  Hour movers + stats / volume; trail from peak.
**E3 — Scalp**  BTC, ETH, SOL, XRP, DOGE: short momentum; tight TP/SL/time.
**E4 — Micro HFT**  BTC-USD & ETH-USD only; buy cadence throttled with E1–E3 (default 30s).

Exits: ``_check_exits_only`` / scheduler job ``coinbase_exit_check`` every 5s; ``scan_and_trade`` is buys-only. Per-position: **time stop** (absolute)
→ take-profit → stop-loss → trail (E2 only). Sells are not gated by the buy rate limiter.
Sells retry once; on failure ``sell_pending`` is set for the next scan.

Logging: ``CB BUY E{n}``, ``CB SELL E{n}``, ``CB CHECK E{n}``, ``CB EXIT E{n}``.

State: ``shark/state/coinbase_positions.json`` (+ trade JSONL).
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

_E3_LIQUID = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD")
_E4_LIQUID = ("BTC-USD", "ETH-USD")
# Gate B momentum fallback when gainer screen is empty (liquid alts).
_GATE_B_FALLBACK_PRODUCTS: Tuple[str, ...] = (
    "XRP-USD",
    "DOGE-USD",
    "ADA-USD",
    "LINK-USD",
    "AVAX-USD",
    "MATIC-USD",
    "DOT-USD",
    "UNI-USD",
)


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


def _min_order_usd(usd_balance: float) -> float:
    raw = (os.environ.get("COINBASE_MIN_ORDER_USD") or "").strip()
    if raw:
        try:
            return max(1e-12, float(raw))
        except (TypeError, ValueError):
            pass
    pct = _env_float("COINBASE_MIN_ORDER_PCT", 0.001)
    return max(1e-9, float(usd_balance) * pct)


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
        min_vol_usd = _env_float("COINBASE_E2_MIN_VOLUME_USD", 10_000.0)
        min_vol_ratio = _env_float(
            "COINBASE_GAINER_VOL_RATIO",
            _env_float("COINBASE_E2_MIN_VOL_RATIO", 1.2),
        )
        max_stats = max(20, int(_env_float("COINBASE_E2_MAX_STATS_FETCH", 80.0)))
        try:
            rows = self._client.list_brokerage_products()
        except Exception as exc:
            logger.warning("Coinbase E2: brokerage products failed: %s", exc)
            rows = []
        vol_map = {
            str(r.get("product_id") or ""): _quote_volume_24h_usd(r)
            for r in rows
            if r.get("product_id")
        }
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

    def emergency_clear_stale_positions(self) -> int:
        """On bot restart: market-sell any open position at or past ``COINBASE_TIME_STOP_MIN``.

        Runs before the scheduler so stuck post-deploy positions are flushed immediately.
        Uses the same sell path as normal exits (no buy rate-limiter gate).
        """
        if not coinbase_enabled():
            return 0
        if not self._client.has_credentials():
            return 0
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)
        positions = list(state.get("positions") or [])
        now = time.time()
        time_stop_sec = _env_float("COINBASE_TIME_STOP_MIN", 5.0) * 60.0

        def _is_stale(pos: Dict[str, Any]) -> bool:
            entry_t = float(pos.get("entry_time") or 0.0)
            if entry_t <= 0:
                return True
            age = now - entry_t
            return age >= time_stop_sec

        stale = [p for p in positions if _is_stale(p)]
        fresh = [p for p in positions if not _is_stale(p)]
        if not stale:
            return 0

        logger.info(
            "Coinbase EMERGENCY: %d position(s) at/ past time stop (%.0fs) — selling now",
            len(stale),
            time_stop_sec,
        )
        scan_ids = _hf_scan_product_ids(self._client, state, now)
        stale_ids = [str(p.get("product_id") or "") for p in stale if p.get("product_id")]
        pids = list(set(scan_ids) | set(collect_price_product_ids(state)) | set(stale_ids))
        try:
            prices = self._client.get_prices_batched(pids)
        except Exception as exc:
            logger.warning("Coinbase EMERGENCY: price fetch failed: %s", exc)
            return 0
        if not prices:
            logger.warning("Coinbase EMERGENCY: no prices — cannot clear stale positions")
            return 0
        self._ingest_prices_into_history(prices, now)

        sold = 0
        failures: List[Dict[str, Any]] = []
        for i, pos in enumerate(stale):
            tail = stale[i + 1 :]
            pid = str(pos.get("product_id") or "")
            entry = float(pos.get("entry_price") or 0.0)
            size_base = float(pos.get("size_base") or 0.0)
            cost_usd = float(pos.get("cost_usd") or 0.0)
            entry_t = float(pos.get("entry_time") or 0.0)
            eng = int(pos.get("engine") or _infer_engine_from_legacy(pos))
            age = (now - entry_t) if entry_t > 0 else -1.0
            if not pid or size_base <= 0:
                logger.warning(
                    "Coinbase EMERGENCY: skip invalid position %s size=%s",
                    pid,
                    size_base,
                )
                failures.append(pos)
                state["positions"] = fresh + failures + tail
                save_coinbase_state(state)
                continue
            if pid not in prices:
                logger.warning(
                    "Coinbase EMERGENCY: no price for %s — keeping position",
                    pid,
                )
                upd = dict(pos)
                upd["sell_pending"] = True
                failures.append(upd)
                state["positions"] = fresh + failures + tail
                save_coinbase_state(state)
                continue

            bid, _ask = prices[pid]
            current = bid
            if entry <= 0:
                pnl_pct = 0.0
            else:
                pnl_pct = (current - entry) / entry
            logger.info(
                "EMERGENCY EXIT: %s age=%.0fs (time stop=%.0fs)",
                pid,
                age,
                time_stop_sec,
            )
            base_str = _fmt_base_size(pid, size_base)
            result = self._try_market_sell_twice(pid, base_str)
            profit_usd = current * size_base - cost_usd

            if result.success:
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
                state["positions"] = fresh + failures + tail
                save_coinbase_state(state)
                logger.info(
                    "Coinbase EMERGENCY SELL ok: %s %+.2f%% $%.4f (emergency_time_stop)",
                    pid,
                    pnl_pct * 100.0,
                    profit_usd,
                )
                _log_trade(
                    {
                        "ts": now,
                        "type": "sell",
                        "engine": eng,
                        "reason": "emergency_time_stop",
                        "order_id": result.order_id,
                        "product_id": pid,
                        "entry_price": entry,
                        "exit_price": current,
                        "pnl_usd": profit_usd,
                    }
                )
                try:
                    from trading_ai.shark.reporting import send_telegram

                    send_telegram(
                        f"🚨 EMERGENCY CB exit (stale): {_cb_sym(pid)} "
                        f"{pnl_pct*100:+.2f}% ${profit_usd:+.3f} (restart flush)"
                    )
                except Exception:
                    pass
            else:
                logger.warning(
                    "Coinbase EMERGENCY SELL failed %s (%s) — sell_pending=True",
                    pid,
                    result.reason,
                )
                upd = dict(pos)
                upd["sell_pending"] = True
                failures.append(upd)
                state["positions"] = fresh + failures + tail
                save_coinbase_state(state)

        remaining = state.get("positions") or []
        logger.info(
            "Coinbase EMERGENCY: completed — sold=%d remaining_open=%d",
            sold,
            len(remaining),
        )
        return sold

    def _try_market_sell_twice(self, product_id: str, base_str: str) -> Any:
        r = self._client.place_market_sell(product_id, base_str)
        if r.success:
            return r
        time.sleep(0.35)
        return self._client.place_market_sell(product_id, base_str)

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

    def _exit_params(self, engine: int) -> Tuple[float, float, float, float]:
        """Returns (profit_pct, stop_pct, time_min, trail_pct). trail only used for E2."""
        if engine == 1:
            return (
                _env_float("COINBASE_E1_PROFIT_PCT", 0.02),
                _env_float("COINBASE_E1_STOP_PCT", 0.01),
                _env_float("COINBASE_E1_TIME_MIN", 10.0),
                0.0,
            )
        if engine == 2:
            return (
                _env_float("COINBASE_E2_PROFIT_PCT", 0.50),
                _env_float("COINBASE_E2_STOP_PCT", 0.10),
                _env_float("COINBASE_E2_TIME_MIN", 120.0),
                _env_float("COINBASE_E2_TRAIL_PCT", 0.10),
            )
        if engine == 3:
            return (
                _env_float("COINBASE_E3_PROFIT_PCT", 0.005),
                _env_float("COINBASE_E3_STOP_PCT", 0.003),
                _env_float("COINBASE_E3_TIME_MIN", 5.0),
                0.0,
            )
        return (
            _env_float("COINBASE_E4_PROFIT_PCT", 0.0015),
            _env_float("COINBASE_E4_STOP_PCT", 0.001),
            _env_float("COINBASE_E4_TIME_MIN", 3.0),
            0.0,
        )

    def _check_exits(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        now: float,
    ) -> int:
        # One full pass over all open positions — no break after a successful sell
        # (time-stop and other exits can flush many positions in a single scan).
        positions_snapshot = list(state.get("positions") or [])
        remaining: List[Dict[str, Any]] = []
        exits = 0
        for idx, pos in enumerate(positions_snapshot):
            pid = str(pos.get("product_id") or "")
            entry = float(pos.get("entry_price") or 0.0)
            size_base = float(pos.get("size_base") or 0.0)
            cost_usd = float(pos.get("cost_usd") or 0.0)
            entry_t = float(pos.get("entry_time") or 0.0)
            eng = int(pos.get("engine") or _infer_engine_from_legacy(pos))
            sell_pending = bool(pos.get("sell_pending"))
            gate = str(pos.get("gate") or "").strip().upper()

            if pid not in prices:
                logger.info(
                    "CB CHECK E%d: %s pnl=n/a age=%ds (no price)",
                    eng,
                    pid,
                    int(now - entry_t) if entry_t else -1,
                )
                remaining.append(pos)
                continue

            bid, _ask = prices[pid]
            current = bid
            if entry <= 0 or size_base <= 0:
                logger.info(
                    "CB CHECK E%d: %s pnl=n/a age=%ds (invalid entry/size)",
                    eng,
                    pid,
                    int(now - entry_t) if entry_t else -1,
                )
                remaining.append(pos)
                continue

            pnl_pct = (current - entry) / entry
            age_s = int(now - entry_t) if entry_t > 0 else -1
            log_label = f"Gate {gate}" if gate in ("A", "B") else f"E{eng}"
            logger.info(
                "CB CHECK %s: %s pnl=%+.3f%% age=%ds pending=%s",
                log_label,
                pid,
                pnl_pct * 100.0,
                age_s,
                sell_pending,
            )

            if gate in ("A", "B"):
                tp = _env_float("COINBASE_PROFIT_TARGET_PCT", 0.005)
                sl = _env_float("COINBASE_STOP_LOSS_PCT", 0.01)
                tmin = _env_float("COINBASE_TIME_STOP_MIN", 5.0)
                trail = 0.0
            else:
                tp, sl, tmin, trail = self._exit_params(eng)
            tlim = tmin * 60.0
            peak = max(float(pos.get("peak_price") or entry), current)

            # Time stop is absolute: fire before take-profit / stop / trail.
            sell_reason = ""
            if sell_pending:
                sell_reason = "sell_pending_retry"
            elif entry_t > 0 and tlim > 0 and (now - entry_t) >= tlim:
                sell_reason = f"time>={tmin:.0f}m"
            elif pnl_pct >= tp:
                sell_reason = f"tp +{tp*100:.2f}%"
            elif pnl_pct <= -sl:
                sell_reason = f"stop -{sl*100:.2f}%"
            elif eng == 2 and trail > 0 and gate not in ("A", "B") and current < peak * (1.0 - trail):
                sell_reason = f"trail -{trail*100:.0f}% peak"

            if not sell_reason:
                upd = dict(pos)
                upd["peak_price"] = peak
                if eng == 2 and gate not in ("A", "B") and pnl_pct >= 0.25 and not pos.get("ride2_tg"):
                    upd["ride2_tg"] = True
                    try:
                        from trading_ai.shark.reporting import send_telegram

                        send_telegram(
                            f"🚀 E2 RIDING: {_cb_sym(pid)} now +{pnl_pct*100:.0f}% from entry"
                        )
                    except Exception:
                        pass
                remaining.append(upd)
                continue

            # Exits must not be blocked by the per-minute buy rate limiter.
            base_str = _fmt_base_size(pid, size_base)
            result = self._try_market_sell_twice(pid, base_str)
            profit_usd = current * size_base - cost_usd

            if result.success:
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

                xl = f"Gate {gate}" if gate in ("A", "B") else f"E{eng}"
                logger.info(
                    "CB EXIT %s: %s %+.2f%% $%.4f profit (%s)",
                    xl,
                    pid,
                    pnl_pct * 100.0,
                    profit_usd,
                    sell_reason,
                )
                logger.info(
                    "CB SELL %s: %s %+.2f%% $%.4f profit",
                    xl,
                    pid,
                    pnl_pct * 100.0,
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
                        "exit_price": current,
                        "pnl_usd": profit_usd,
                    }
                )
                try:
                    from trading_ai.shark.reporting import send_telegram

                    sym = _cb_sym(pid)
                    if gate in ("A", "B"):
                        send_telegram(
                            f"{'💰' if profit_usd >= 0 else '🛑'} Gate {gate}: {sym} "
                            f"{pnl_pct*100:+.2f}% ${profit_usd:+.3f} ({sell_reason})"
                        )
                    elif eng == 1:
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
                            send_telegram(f"🛑 E2 TRAIL: {sym} -10% from peak sold")
                        else:
                            send_telegram(
                                f"💰 E2 WIN: {sym} +{pnl_pct*100:.1f}% profit ${abs(profit_usd):.2f}"
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
                state["positions"] = remaining + positions_snapshot[idx + 1 :]
                save_coinbase_state(state)
            else:
                logger.warning(
                    "CB SELL failed E%d %s (%s) — sell_pending=True",
                    eng,
                    pid,
                    result.reason,
                )
                upd = dict(pos)
                upd["sell_pending"] = True
                upd["peak_price"] = peak
                remaining.append(upd)
                state["positions"] = remaining + positions_snapshot[idx + 1 :]
                save_coinbase_state(state)

        state["positions"] = remaining
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
        }
        if gate:
            pos["gate"] = gate
            pos["position_pct"] = position_pct
            if strategy:
                pos["strategy"] = strategy
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
            if not self._rate_limiter.allow():
                break
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
            if not self._rate_limiter.allow():
                break
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
            if not self._rate_limiter.allow():
                break
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
            if not self._rate_limiter.allow():
                break
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
    ) -> None:
        if not _coinbase_gates_mode():
            return
        if not _env_bool("COINBASE_GATE_A_ENABLED", True):
            return
        products = _parse_csv_products(
            os.environ.get("COINBASE_GATE_A_PRODUCTS") or "BTC-USD,ETH-USD,SOL-USD"
        )
        max_n = max(1, int(_env_float("COINBASE_GATE_A_POSITIONS", 10.0)))
        pct = _env_float("COINBASE_GATE_A_POSITION_PCT", 0.05)
        dip_pct = _env_float("COINBASE_GATE_A_DIP_PCT", 0.002)
        mom_pct = _env_float("COINBASE_GATE_A_MOM_PCT", 0.001)
        order_usd = _gate_order_usd(usd_balance, pct)
        eng = 3

        while _count_gate(state, "A") < max_n:
            placed = False
            for pid in products:
                if pid not in prices:
                    continue
                if _count_gate(state, "A") >= max_n:
                    break
                bid, ask = prices[pid]
                if bid <= 0 and ask <= 0:
                    continue
                mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
                hist = list(self._price_history.get(pid) or [])
                ref2 = _mid_at_or_before(hist, now - 120.0)
                ref60 = _mid_at_or_before(hist, now - 60.0)
                signal = ""
                if ref2 and ref2 > 0 and (mid - ref2) / ref2 <= -dip_pct:
                    signal = "dip"
                elif ref60 and ref60 > 0 and (mid - ref60) / ref60 >= mom_pct:
                    signal = "mom"
                if not signal:
                    continue
                ok, _ = _can_buy_gate(state, "A", pid, order_usd, usd_balance, max_n)
                if not ok:
                    continue
                if not self._rate_limiter.allow():
                    return
                r = self._client.place_market_buy(pid, order_usd)
                if not r.success:
                    continue
                placed = True
                self._append_buy(
                    state,
                    engine=eng,
                    product_id=pid,
                    mid=mid,
                    order_usd=order_usd,
                    now=now,
                    order_id=r.order_id,
                    gate="A",
                    position_pct=pct,
                    strategy=signal,
                )
                try:
                    from trading_ai.shark.reporting import send_telegram

                    send_telegram(
                        f"🟢 Gate A ({signal}): {_cb_sym(pid)} ${order_usd:.2f} @ ${mid:.4f}"
                    )
                except Exception:
                    pass
                break
            if not placed:
                break

    def _gate_b_fallback_momentum_pass(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
        max_n: int,
        pct: float,
        order_usd: float,
        eng: int,
    ) -> bool:
        """One pass over fallback alts: momentum ≥ threshold in last 60s. Returns True if any buy."""
        raw = os.environ.get("COINBASE_GATE_B_FALLBACK_PRODUCTS")
        pids = _parse_csv_products(raw) if (raw or "").strip() else list(_GATE_B_FALLBACK_PRODUCTS)
        mom_pct = _env_float("COINBASE_GAINER_FALLBACK_MOM_PCT", 0.001)
        any_buy = False
        for pid in pids:
            if _count_gate(state, "B") >= max_n:
                break
            if pid not in prices:
                continue
            bid, ask = prices[pid]
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = list(self._price_history.get(pid) or [])
            ref60 = _mid_at_or_before(hist, now - 60.0)
            if ref60 is None or ref60 <= 0:
                continue
            if (mid - ref60) / ref60 < mom_pct:
                continue
            ok, _ = _can_buy_gate(state, "B", pid, order_usd, usd_balance, max_n)
            if not ok:
                continue
            if not self._rate_limiter.allow():
                return any_buy
            r = self._client.place_market_buy(pid, order_usd)
            if not r.success:
                continue
            any_buy = True
            self._append_buy(
                state,
                engine=eng,
                product_id=pid,
                mid=mid,
                order_usd=order_usd,
                now=now,
                order_id=r.order_id,
                gate="B",
                position_pct=pct,
                strategy="mom_fb",
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                send_telegram(
                    f"🎯 Gate B (mom): {_cb_sym(pid)} +{(mid - ref60) / ref60 * 100:.2f}%/60s "
                    f"${order_usd:.2f} @ ${mid:.4f}"
                )
            except Exception:
                pass
            if _count_gate(state, "B") >= max_n:
                break
        return any_buy

    def _gate_b_gainer(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        usd_balance: float,
        now: float,
    ) -> None:
        if not _coinbase_gates_mode():
            return
        if not _env_bool("COINBASE_GATE_B_ENABLED", True):
            return
        max_n = max(1, int(_env_float("COINBASE_GATE_B_POSITIONS", 10.0)))
        interval = _env_float("COINBASE_GATE_B_SCAN_INTERVAL", 0.0)
        last_ts = float(state.get("gate_b_scan_ts") or 0.0)
        if interval > 0 and last_ts > 0 and (now - last_ts) < interval:
            return
        state["gate_b_scan_ts"] = now

        pct = _env_float("COINBASE_GATE_B_POSITION_PCT", 0.05)
        order_usd = _gate_order_usd(usd_balance, pct)
        dip_from_high = _env_float("COINBASE_GAINER_DIP_PCT", 0.005)
        ranked = self._e2_candidates(state, prices, now)
        eng = 2

        while _count_gate(state, "B") < max_n:
            placed = False
            for row in ranked:
                if _count_gate(state, "B") >= max_n:
                    break
                pid = row["product_id"]
                mid = float(row["mid"])
                st = self._cached_exchange_stats(pid, now)
                if not st:
                    continue
                try:
                    high = float(st.get("high") or 0.0)
                    last_px = float(st.get("last") or 0.0)
                except (TypeError, ValueError):
                    continue
                if high > 0 and last_px > high * (1.0 - dip_from_high):
                    continue
                hg = float(row["hour_pct"])
                ok, _ = _can_buy_gate(state, "B", pid, order_usd, usd_balance, max_n)
                if not ok:
                    continue
                if not self._rate_limiter.allow():
                    return
                r = self._client.place_market_buy(pid, order_usd)
                if not r.success:
                    continue
                placed = True
                self._append_buy(
                    state,
                    engine=eng,
                    product_id=pid,
                    mid=mid,
                    order_usd=order_usd,
                    now=now,
                    order_id=r.order_id,
                    gate="B",
                    position_pct=pct,
                    strategy="gainer",
                )
                try:
                    from trading_ai.shark.reporting import send_telegram

                    send_telegram(
                        f"🎯 Gate B: {_cb_sym(pid)} +{hg*100:.1f}%/hr pullback ${order_usd:.2f} @ ${mid:.4f}"
                    )
                except Exception:
                    pass
                break
            if _count_gate(state, "B") >= max_n:
                break
            if placed:
                continue
            if not self._gate_b_fallback_momentum_pass(
                state, prices, usd_balance, now, max_n, pct, order_usd, eng
            ):
                break

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
            ga = _parse_csv_products(
                os.environ.get("COINBASE_GATE_A_PRODUCTS") or "BTC-USD,ETH-USD,SOL-USD"
            )
            fb = _parse_csv_products(
                os.environ.get("COINBASE_GATE_B_FALLBACK_PRODUCTS")
                or ",".join(_GATE_B_FALLBACK_PRODUCTS)
            )
            pids = list(set(pids) | set(ga) | set(fb))

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

        # Buys only — exits run on scheduler job ``coinbase_exit_check`` (~5s).
        if _coinbase_gates_mode():
            if run_buy_tick:
                self._gate_a_scan(state, prices, usd_balance, now)
                self._gate_b_gainer(state, prices, usd_balance, now)
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
            "exits_this_scan": 0,
            "exits": 0,
        }
