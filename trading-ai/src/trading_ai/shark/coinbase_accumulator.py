"""
Coinbase 24/7 spot accumulation engine.

Three strategies run on every 60-second scan:

  A  Dip accumulator  — buy when price drops ≥1% in 5 min; sell at +3% or stop.
  B  Grid trading     — buy every COINBASE_GRID_STEP_USD price move; sell each
                        lot at +3%.  Max 5 grid lots per instrument.
  C  Momentum scalp   — buy when price rises ≥0.5% in 2 min; sell at +1%/-0.5%.

Hard safety limits (cannot be overridden):
  COINBASE_MAX_TOTAL_USD   — max total open-position cost (default $500)
  COINBASE_MAX_DAILY_LOSS  — halt all trading if daily realised loss hits this (default $50)
  20% cash reserve         — never deploy more than 80% of account USD balance
  10 orders / minute       — Coinbase Advanced Trade rate limit
  All orders logged to ~/.ezras-runtime/shark/logs/coinbase_trade_log.jsonl

State persists across restarts in:
  ~/.ezras-runtime/shark/state/coinbase_positions.json
"""

from __future__ import annotations

import json
import logging
import math
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


# ── state I/O ────────────────────────────────────────────────────────────────


def _default_state() -> Dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "positions": [],
        "daily_pnl_usd": 0.0,
        "daily_pnl_date": today,
        "total_realized_usd": 0.0,
        "grid_state": {},
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
    """Token bucket: at most ``max_per_minute`` order calls per rolling 60-second window."""

    def __init__(self, max_per_minute: int = 10) -> None:
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


# ── daily P&L reset ───────────────────────────────────────────────────────────


def _reset_daily_pnl_if_needed(state: Dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl_usd"] = 0.0
        state["daily_pnl_date"] = today


# ── product budgets ───────────────────────────────────────────────────────────

_PRODUCT_BUDGETS: Dict[str, Tuple[str, float]] = {
    "BTC-USD": ("COINBASE_BTC_BUDGET", 100.0),
    "ETH-USD": ("COINBASE_ETH_BUDGET", 100.0),
    "SOL-USD": ("COINBASE_SOL_BUDGET", 50.0),
}


def _get_budget(product_id: str) -> float:
    env_key, default = _PRODUCT_BUDGETS.get(product_id, ("", 0.0))
    return _env_float(env_key, default) if env_key else default


def _all_products() -> List[str]:
    return list(_PRODUCT_BUDGETS.keys())


# ── safety checks ─────────────────────────────────────────────────────────────


def _total_open_cost(state: Dict[str, Any]) -> float:
    return sum(float(p.get("cost_usd") or 0.0) for p in state.get("positions") or [])


def _product_open_cost(state: Dict[str, Any], product_id: str) -> float:
    return sum(
        float(p.get("cost_usd") or 0.0)
        for p in (state.get("positions") or [])
        if p.get("product_id") == product_id
    )


def _can_buy(
    state: Dict[str, Any], product_id: str, usd_amount: float, usd_balance: float
) -> Tuple[bool, str]:
    """Check all hard safety gates. Returns (allowed, reason)."""
    max_total = _env_float("COINBASE_MAX_TOTAL_USD", 500.0)
    max_daily_loss = _env_float("COINBASE_MAX_DAILY_LOSS", 50.0)
    max_open = int(_env_float("COINBASE_MAX_OPEN_POSITIONS", 10.0))
    min_order = _env_float("COINBASE_MIN_ORDER_USD", 5.0)

    # 1. Daily loss gate
    daily_loss = -(float(state.get("daily_pnl_usd") or 0.0))
    if daily_loss >= max_daily_loss:
        return False, f"daily loss limit ${max_daily_loss:.0f} hit (${daily_loss:.2f} today)"

    # 2. Minimum order size
    if usd_amount < min_order:
        return False, f"order ${usd_amount:.2f} below minimum ${min_order:.2f}"

    # 3. Max open positions
    open_count = len(state.get("positions") or [])
    if open_count >= max_open:
        return False, f"max open positions ({max_open}) reached"

    # 4. Max total USD exposure
    current_exposure = _total_open_cost(state)
    if current_exposure + usd_amount > max_total:
        return False, f"would exceed COINBASE_MAX_TOTAL_USD ${max_total:.0f}"

    # 5. Per-instrument budget
    budget = _get_budget(product_id)
    if budget > 0:
        product_cost = _product_open_cost(state, product_id)
        if product_cost + usd_amount > budget:
            return False, (
                f"{product_id} budget ${budget:.0f} would be exceeded "
                f"(${product_cost:.2f} already open)"
            )

    # 6. 20% cash reserve
    reserve = usd_balance * 0.20
    available = usd_balance - reserve
    if usd_amount > available:
        return False, (
            f"20% cash reserve: need ${usd_amount:.2f} but "
            f"only ${available:.2f} deployable (balance ${usd_balance:.2f})"
        )

    return True, "ok"


# ── base-size formatting ──────────────────────────────────────────────────────


def _fmt_base_size(product_id: str, size: float) -> str:
    """Decimal precision required by Coinbase for each asset."""
    if product_id.startswith("BTC"):
        return f"{size:.8f}"
    if product_id.startswith("ETH"):
        return f"{size:.6f}"
    return f"{size:.4f}"  # SOL, etc.


# ─────────────────────────────────────────────────────────────────────────────
# CoinbaseAccumulator
# ─────────────────────────────────────────────────────────────────────────────


class CoinbaseAccumulator:
    """
    24/7 spot crypto accumulation engine.

    Usage::

        acc = CoinbaseAccumulator()
        acc.load_and_check_positions_on_startup()   # call once at boot
        # APScheduler calls every 60 s:
        acc.scan_and_trade()
    """

    def __init__(self, client: Optional[CoinbaseClient] = None) -> None:
        self._client = client or CoinbaseClient()
        self._rate_limiter = _RateLimiter(max_per_minute=10)
        # price_history: product_id → deque of (unix_ts, mid_price)
        self._price_history: Dict[str, deque[Tuple[float, float]]] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def scan_and_trade(self) -> None:
        """Main entry — safe wrapper; swallows non-auth exceptions."""
        if not coinbase_enabled():
            return
        if not self._client.has_credentials():
            logger.debug("Coinbase: no credentials configured, scan skipped")
            return
        try:
            self._scan()
        except CoinbaseAuthError as exc:
            logger.error("Coinbase auth error (check keys): %s", exc)
        except Exception as exc:
            logger.warning("Coinbase scan error (non-fatal): %s", exc)

    def get_summary(self) -> Dict[str, Any]:
        """Snapshot for hourly reports — reads state file directly."""
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
        """
        On restart: reload state, price all open positions, close any that already
        hit profit / stop-loss targets.
        """
        if not coinbase_enabled():
            return
        if not self._client.has_credentials():
            return
        logger.info("Coinbase: startup position check ...")
        try:
            state = load_coinbase_state()
            positions = state.get("positions") or []
            if not positions:
                logger.info("Coinbase: no open positions on startup")
                return
            pids = list({p.get("product_id") for p in positions if p.get("product_id")})
            prices = self._client.get_prices(pids)
            _reset_daily_pnl_if_needed(state)
            self._check_exits(state, prices, time.time())
            save_coinbase_state(state)
            remaining = len(state.get("positions") or [])
            logger.info(
                "Coinbase startup: %d/%d positions remain after exit scan",
                remaining,
                len(positions),
            )
        except Exception as exc:
            logger.warning("Coinbase startup position check failed (non-fatal): %s", exc)

    # ── internal scan ─────────────────────────────────────────────────────────

    def _scan(self) -> None:
        state = load_coinbase_state()
        _reset_daily_pnl_if_needed(state)

        # ── fetch prices ──────────────────────────────────────────────────────
        try:
            prices = self._client.get_prices(_all_products())
        except Exception as exc:
            logger.warning("Coinbase price fetch failed: %s", exc)
            return

        if not prices:
            logger.debug("Coinbase: no price data returned")
            return

        # ── fetch available USD balance (for safety gates) ────────────────────
        try:
            usd_balance = self._client.get_usd_balance()
        except Exception:
            usd_balance = 0.0

        now = time.time()

        # ── update rolling price history (10-minute window) ───────────────────
        for pid, (bid, ask) in prices.items():
            if bid <= 0 and ask <= 0:
                continue
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            hist = self._price_history.setdefault(pid, deque())
            hist.append((now, mid))
            cutoff = now - 600
            while hist and hist[0][0] < cutoff:
                hist.popleft()

        # ── check exits on all open positions ─────────────────────────────────
        self._check_exits(state, prices, now)

        # ── buy strategies ────────────────────────────────────────────────────
        for pid in _all_products():
            if pid not in prices:
                continue
            bid, ask = prices[pid]
            if bid <= 0 or ask <= 0:
                continue
            mid = (bid + ask) / 2.0

            # Strategy A: dip buy (always active when COINBASE_ENABLED)
            self._strategy_a(state, pid, mid, usd_balance, now)

            # Strategy B: grid (COINBASE_GRID_ENABLED, default true)
            if _env_bool("COINBASE_GRID_ENABLED", True):
                self._strategy_b(state, pid, mid, usd_balance)

            # Strategy C: momentum scalp (COINBASE_MOMENTUM_ENABLED, default true)
            if _env_bool("COINBASE_MOMENTUM_ENABLED", True):
                self._strategy_c(state, pid, mid, usd_balance, now)

        save_coinbase_state(state)

    # ── exit checker ──────────────────────────────────────────────────────────

    def _check_exits(
        self,
        state: Dict[str, Any],
        prices: Dict[str, Tuple[float, float]],
        now: float,
    ) -> None:
        profit_target = _env_float("COINBASE_PROFIT_TARGET_PCT", 0.03)
        stop_loss_pct = _env_float("COINBASE_STOP_LOSS_PCT", 0.15)

        remaining: List[Dict[str, Any]] = []
        for pos in list(state.get("positions") or []):
            pid = pos.get("product_id") or ""
            if pid not in prices:
                remaining.append(pos)
                continue

            bid, _ask = prices[pid]
            current = bid  # sell at bid
            entry = float(pos.get("entry_price") or 0.0)
            size_base = float(pos.get("size_base") or 0.0)
            cost_usd = float(pos.get("cost_usd") or 0.0)
            strategy = str(pos.get("strategy") or "A")

            if entry <= 0 or size_base <= 0:
                remaining.append(pos)
                continue

            gain_pct = (current - entry) / entry

            # Strategy C has tighter targets (scalp)
            if strategy == "C":
                take_profit = 0.01    # +1%
                stop = -0.005         # -0.5%
            else:
                take_profit = profit_target
                stop = -stop_loss_pct

            should_sell = gain_pct >= take_profit or gain_pct <= stop
            if not should_sell:
                remaining.append(pos)
                continue

            if not self._rate_limiter.allow():
                logger.warning("Coinbase rate limit — deferring exit for %s", pid)
                remaining.append(pos)
                continue

            sell_reason = (
                f"profit +{gain_pct*100:.2f}%"
                if gain_pct >= take_profit
                else f"stop {gain_pct*100:.2f}%"
            )
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

                sign = "+" if pnl >= 0 else ""
                logger.info(
                    "Coinbase %s SELL: %s %s @ $%.2f | entry $%.2f | PnL %s$%.2f (%s)",
                    strategy,
                    pid,
                    base_str,
                    current,
                    entry,
                    sign,
                    abs(pnl),
                    sell_reason,
                )

                # Telegram alert
                try:
                    from trading_ai.shark.reporting import send_telegram

                    emoji = "✅" if pnl >= 0 else "❌"
                    send_telegram(
                        f"{emoji} COINBASE {strategy} EXIT: {pid}\n"
                        f"  Exit ${current:,.2f} | Entry ${entry:,.2f}\n"
                        f"  PnL {sign}${abs(pnl):.2f} ({gain_pct*100:+.2f}%)\n"
                        f"  Reason: {sell_reason}"
                    )
                except Exception:
                    pass

                # Remove from grid filled_levels for B positions
                if strategy == "B":
                    grid_level = pos.get("grid_level")
                    if grid_level is not None:
                        gs = (
                            state.setdefault("grid_state", {})
                            .setdefault(pid, {})
                        )
                        fl: List[float] = gs.get("filled_levels") or []
                        try:
                            fl.remove(grid_level)
                        except ValueError:
                            pass
                        gs["filled_levels"] = fl
            else:
                logger.warning(
                    "Coinbase SELL failed (%s): %s — keeping position", pid, result.reason
                )
                remaining.append(pos)

        state["positions"] = remaining

    # ── Strategy A: dip accumulator ───────────────────────────────────────────

    def _strategy_a(
        self,
        state: Dict[str, Any],
        product_id: str,
        current_price: float,
        usd_balance: float,
        now: float,
    ) -> None:
        """Buy when price drops ≥1% in the last 5 minutes."""
        hist = list(self._price_history.get(product_id) or [])
        if len(hist) < 2:
            return

        cutoff = now - 300  # 5-minute lookback
        old = [(t, p) for t, p in hist if t <= cutoff]
        if not old:
            return
        ref_price = old[-1][1]
        if ref_price <= 0:
            return

        change_pct = (current_price - ref_price) / ref_price
        if change_pct > -0.01:  # need ≥1% drop
            return

        # Don't stack A positions opened in the last 10 minutes on the same product
        recent_cutoff = now - 600
        has_recent = any(
            p.get("product_id") == product_id
            and p.get("strategy") == "A"
            and float(p.get("entry_time") or 0) >= recent_cutoff
            for p in (state.get("positions") or [])
        )
        if has_recent:
            return

        budget = _get_budget(product_id)
        min_order = _env_float("COINBASE_MIN_ORDER_USD", 5.0)
        order_usd = max(min_order, min(budget * 0.25, 25.0))

        ok, reason = _can_buy(state, product_id, order_usd, usd_balance)
        if not ok:
            logger.debug("Coinbase A blocked (%s): %s", product_id, reason)
            return
        if not self._rate_limiter.allow():
            return

        result = self._client.place_market_buy(product_id, order_usd)
        if not result.success:
            return

        pos: Dict[str, Any] = {
            "order_id": result.order_id,
            "product_id": product_id,
            "entry_price": current_price,
            "size_base": order_usd / current_price,
            "cost_usd": order_usd,
            "entry_time": now,
            "strategy": "A",
            "grid_level": None,
            "trigger_pct": change_pct,
        }
        state.setdefault("positions", []).append(pos)
        _log_trade(
            {
                "ts": now,
                "type": "buy",
                "strategy": "A",
                "order_id": result.order_id,
                "product_id": product_id,
                "entry_price": current_price,
                "cost_usd": order_usd,
                "size_base": pos["size_base"],
                "trigger": f"dip {change_pct*100:.2f}%",
            }
        )
        logger.info(
            "Coinbase A BUY: %s $%.2f @ $%.2f (dip %.2f%%)",
            product_id,
            order_usd,
            current_price,
            change_pct * 100,
        )
        try:
            from trading_ai.shark.reporting import send_telegram

            send_telegram(
                f"🟢 COINBASE A BUY: {product_id}\n"
                f"  Price ${current_price:,.2f} (dip {change_pct*100:.2f}%)\n"
                f"  Size ${order_usd:.2f} | Target ${current_price*1.03:,.2f}"
            )
        except Exception:
            pass

    # ── Strategy B: grid trading ──────────────────────────────────────────────

    def _strategy_b(
        self,
        state: Dict[str, Any],
        product_id: str,
        current_price: float,
        usd_balance: float,
    ) -> None:
        """Buy at each new grid level; sell each lot on +3%."""
        grid_step = _env_float("COINBASE_GRID_STEP_USD", 500.0)
        order_usd = _env_float("COINBASE_GRID_ORDER_USD", 25.0)

        # Current price level (floor to nearest step)
        grid_level = math.floor(current_price / grid_step) * grid_step

        gs: Dict[str, Any] = (
            state.setdefault("grid_state", {}).setdefault(product_id, {})
        )
        filled: List[float] = gs.get("filled_levels") or []

        if grid_level in filled:
            return  # already have a buy at this level

        # Max 5 grid lots per product
        grid_count = sum(
            1
            for p in (state.get("positions") or [])
            if p.get("product_id") == product_id and p.get("strategy") == "B"
        )
        if grid_count >= 5:
            return

        ok, reason = _can_buy(state, product_id, order_usd, usd_balance)
        if not ok:
            logger.debug(
                "Coinbase B blocked (%s @%.0f): %s", product_id, grid_level, reason
            )
            return
        if not self._rate_limiter.allow():
            return

        result = self._client.place_market_buy(product_id, order_usd)
        if not result.success:
            return

        now = time.time()
        pos: Dict[str, Any] = {
            "order_id": result.order_id,
            "product_id": product_id,
            "entry_price": current_price,
            "size_base": order_usd / current_price,
            "cost_usd": order_usd,
            "entry_time": now,
            "strategy": "B",
            "grid_level": grid_level,
        }
        state.setdefault("positions", []).append(pos)
        filled.append(grid_level)
        gs["filled_levels"] = filled
        _log_trade(
            {
                "ts": now,
                "type": "buy",
                "strategy": "B",
                "order_id": result.order_id,
                "product_id": product_id,
                "entry_price": current_price,
                "cost_usd": order_usd,
                "size_base": pos["size_base"],
                "grid_level": grid_level,
            }
        )
        logger.info(
            "Coinbase B BUY: %s $%.2f @ $%.2f (grid level %.0f)",
            product_id,
            order_usd,
            current_price,
            grid_level,
        )

    # ── Strategy C: momentum scalp ────────────────────────────────────────────

    def _strategy_c(
        self,
        state: Dict[str, Any],
        product_id: str,
        current_price: float,
        usd_balance: float,
        now: float,
    ) -> None:
        """Buy on +0.5% move in 2 minutes; exit at +1% or -0.5%."""
        hist = list(self._price_history.get(product_id) or [])
        if len(hist) < 2:
            return

        cutoff = now - 120  # 2-minute lookback
        old = [(t, p) for t, p in hist if t <= cutoff]
        if not old:
            return
        ref_price = old[-1][1]
        if ref_price <= 0:
            return

        change_pct = (current_price - ref_price) / ref_price
        if change_pct < 0.005:  # need ≥0.5% up move
            return

        # Only one C position per product at a time
        has_c = any(
            p.get("product_id") == product_id and p.get("strategy") == "C"
            for p in (state.get("positions") or [])
        )
        if has_c:
            return

        min_order = _env_float("COINBASE_MIN_ORDER_USD", 5.0)
        order_usd = max(min_order, 10.0)

        ok, reason = _can_buy(state, product_id, order_usd, usd_balance)
        if not ok:
            logger.debug("Coinbase C blocked (%s): %s", product_id, reason)
            return
        if not self._rate_limiter.allow():
            return

        result = self._client.place_market_buy(product_id, order_usd)
        if not result.success:
            return

        pos: Dict[str, Any] = {
            "order_id": result.order_id,
            "product_id": product_id,
            "entry_price": current_price,
            "size_base": order_usd / current_price,
            "cost_usd": order_usd,
            "entry_time": now,
            "strategy": "C",
            "grid_level": None,
            "trigger_pct": change_pct,
        }
        state.setdefault("positions", []).append(pos)
        _log_trade(
            {
                "ts": now,
                "type": "buy",
                "strategy": "C",
                "order_id": result.order_id,
                "product_id": product_id,
                "entry_price": current_price,
                "cost_usd": order_usd,
                "size_base": pos["size_base"],
                "trigger": f"momentum +{change_pct*100:.2f}%",
            }
        )
        logger.info(
            "Coinbase C BUY: %s $%.2f @ $%.2f (momentum +%.2f%%)",
            product_id,
            order_usd,
            current_price,
            change_pct * 100,
        )
