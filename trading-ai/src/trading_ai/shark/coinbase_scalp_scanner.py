"""
Scanner: BTC-USD / ETH-USD momentum + liquidity + spread filter. Does not manage exits.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from trading_ai.shark.coinbase_scalp_config import CoinbaseScalpConfig
from trading_ai.shark.coinbase_scalp_position_manager import new_trade_from_buy, reset_daily_if_needed
from trading_ai.shark.outlets.coinbase import CoinbaseClient

logger = logging.getLogger(__name__)


def _mid(bid: float, ask: float) -> float:
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return bid or ask or 0.0


def _spread_pct(bid: float, ask: float) -> float:
    mid = _mid(bid, ask)
    if mid <= 0:
        return 1.0
    return (ask - bid) / mid if ask > bid else 0.0


def _quote_volume_24h(row: Dict[str, Any]) -> float:
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


class CoinbaseScalpScanner:
    def __init__(
        self,
        client: CoinbaseClient,
        config: CoinbaseScalpConfig,
        price_history: Dict[str, Deque[Tuple[float, float]]],
    ) -> None:
        self._client = client
        self._cfg = config
        self._history = price_history
        self._vol_cache: Dict[str, float] = {}
        self._vol_cache_ts: float = 0.0

    def _ensure_history(self, product_id: str, mid: float, now: float) -> None:
        hist = self._history.setdefault(product_id, deque())
        hist.append((now, mid))
        keep = max(120.0, self._cfg.momentum_lookback_seconds * 2)
        cutoff = now - keep
        while hist and hist[0][0] < cutoff:
            hist.popleft()

    def _ref_mid(self, product_id: str, now: float) -> Optional[float]:
        hist = list(self._history.get(product_id) or [])
        target = now - self._cfg.momentum_lookback_seconds
        old = [(t, p) for t, p in hist if t <= target]
        if not old:
            return None
        return float(old[-1][1])

    def _refresh_volumes(self, now: float) -> None:
        if self._vol_cache and now - self._vol_cache_ts < 45.0:
            return
        try:
            rows = self._client.list_brokerage_products()
        except Exception as exc:
            logger.warning("scalp scanner volume fetch: %s", exc)
            return
        self._vol_cache = {}
        self._vol_cache_ts = now
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("product_id") or "")
            if pid in self._cfg.allowed_products:
                self._vol_cache[pid] = _quote_volume_24h(row)

    def active_position_count(self, state: Dict[str, Any]) -> int:
        n = 0
        for p in state.get("positions") or []:
            if str(p.get("status") or "") in ("NEW", "OPEN", "EXIT_PENDING"):
                n += 1
        return n

    def scan_and_maybe_enter(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        reset_daily_if_needed(state)
        self._refresh_volumes(time.time())

        if self.active_position_count(state) >= self._cfg.max_open_positions:
            return {"ok": True, "skipped": "max_positions"}

        daily = float(state.get("daily_pnl_usd") or 0.0)
        if daily <= -abs(self._cfg.daily_loss_limit_usd):
            return {"ok": True, "skipped": "daily_loss_limit"}

        if int(state.get("consecutive_losses") or 0) >= self._cfg.max_consecutive_losses:
            return {"ok": True, "skipped": "consecutive_losses"}

        try:
            usd_balance = self._client.get_usd_balance()
        except Exception as exc:
            logger.warning("scalp USD balance: %s", exc)
            usd_balance = 0.0

        order_usd = min(self._cfg.order_usd, max(1.0, usd_balance * 0.95))
        if order_usd < 1.0 or usd_balance < order_usd:
            return {"ok": True, "skipped": "insufficient_balance"}

        now = time.time()
        prices = self._client.get_prices(list(self._cfg.products))
        candidates: List[Dict[str, Any]] = []

        for pid in self._cfg.products:
            if pid not in prices:
                continue
            if any(
                str(p.get("product_id")) == pid
                for p in (state.get("positions") or [])
                if str(p.get("status") or "") in ("NEW", "OPEN", "EXIT_PENDING")
            ):
                continue

            bid, ask = prices[pid]
            sp = _spread_pct(bid, ask)
            if sp > self._cfg.max_spread_pct:
                logger.debug("scalp reject %s spread %.5f", pid, sp)
                continue

            mid = _mid(bid, ask)
            if mid <= 0:
                continue

            vol = float(self._vol_cache.get(pid) or 0.0)
            if vol < self._cfg.min_quote_24h_volume_usd:
                logger.debug("scalp reject %s vol24h %s", pid, vol)
                continue

            self._ensure_history(pid, mid, now)
            ref = self._ref_mid(pid, now)
            if ref is None or ref <= 0:
                continue

            mom = (mid - ref) / ref
            if mom < self._cfg.momentum_trigger_pct:
                continue

            candidates.append(
                {
                    "product_id": pid,
                    "mid": mid,
                    "bid": bid,
                    "ask": ask,
                    "spread_pct": sp,
                    "momentum": mom,
                    "vol24h": vol,
                }
            )

        if not candidates:
            return {"ok": True, "skipped": "no_setup"}

        candidates.sort(key=lambda r: (-r["momentum"], r["spread_pct"]))
        best = candidates[0]
        pid = str(best["product_id"])
        mid = float(best["mid"])

        r = self._client.place_market_buy(pid, order_usd)
        if not r.success:
            logger.warning("scalp BUY failed %s: %s", pid, r.reason)
            return {"ok": False, "error": r.reason}

        trade = new_trade_from_buy(
            product_id=pid,
            entry_price=mid,
            cost_usd=order_usd,
            order_id=r.order_id,
            cfg=self._cfg,
        )
        state.setdefault("positions", []).append(trade)
        logger.info(
            "SCALP BUY trade_id=%s %s $%.2f @ %.4f mom=%.5f",
            trade["trade_id"],
            pid,
            order_usd,
            mid,
            best["momentum"],
        )
        return {"ok": True, "trade": trade}
