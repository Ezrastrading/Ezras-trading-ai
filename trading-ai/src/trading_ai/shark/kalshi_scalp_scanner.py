"""
Periodic scanner: eligible S&P / BTC / ETH markets, scored and ranked (no exit logic).

REST-only order book snapshots by default; swap in a feed that pushes book updates if you add WebSocket.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.shark.kalshi_scalp_config import KalshiScalpConfig
from trading_ai.shark.kalshi_scalp_market_filter import (
    FilterResult,
    LiquiditySnapshot,
    MarketFamily,
    entry_side_price_cents,
    evaluate_scalp_filter,
    mark_price_cents_for_pnl,
    pick_scalp_side,
)
from trading_ai.shark.outlets.kalshi import KalshiClient, _kalshi_market_volume

logger = logging.getLogger(__name__)


@dataclass
class ScalpSetup:
    family: MarketFamily
    market_ticker: str
    side: str
    score: float
    ask_cents: int
    bid_cents: int
    spread_cents: float
    contracts: int
    market_row: Dict[str, Any]
    orderbook: Dict[str, Any]
    liquidity: LiquiditySnapshot


def _score_setup(liq: LiquiditySnapshot, side: str, vol: float) -> float:
    """Higher is better: tight spread, resting size, activity."""
    if side.lower() == "yes":
        sp = liq.spread_cents_yes() or 99.0
        depth = min(liq.yes_bid_sz, liq.yes_ask_sz)
    else:
        sp = liq.spread_cents_no() or 99.0
        depth = min(liq.no_bid_sz, liq.no_ask_sz)
    return (1.0 + math.log1p(vol)) * (depth + 1.0) / (sp + 0.5)


def _contracts_for_deployment(ask_cents: int, deployment_usd: float) -> int:
    ask = max(0.01, min(0.99, ask_cents / 100.0))
    n = math.floor(float(deployment_usd) / ask)
    return max(1, int(n))


class KalshiScalpScanner:
    """Fetches open markets for configured series, filters, ranks, returns best setup or None."""

    def __init__(self, cfg: KalshiScalpConfig, client: Optional[KalshiClient] = None) -> None:
        self.cfg = cfg
        base = cfg.kalshi_api_base or os.environ.get(
            "KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2"
        ).rstrip("/")
        self.client = client or KalshiClient(base_url=base)

    def _fetch_series_markets(self, series: str, limit: int = 120) -> List[Dict[str, Any]]:
        try:
            j = self.client._request(
                "GET",
                "/markets",
                params={"status": "open", "limit": limit, "series_ticker": series},
            )
            batch = j.get("markets") or []
            return [m for m in batch if isinstance(m, dict)]
        except Exception as exc:
            logger.debug("Kalshi scalp series fetch failed %s: %s", series, exc)
            return []

    def _orderbook(self, ticker: str) -> Dict[str, Any]:
        import urllib.parse

        return self.client._request("GET", f"/markets/{urllib.parse.quote(ticker.strip(), safe='')}/orderbook")

    def collect_candidates(self) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for ser in sorted(self.cfg.series_for_families()):
            for row in self._fetch_series_markets(ser):
                tid = str(row.get("ticker") or "").strip()
                if tid:
                    merged[tid] = row
        return list(merged.values())

    def scan_best_setup(self) -> Tuple[Optional[ScalpSetup], Dict[str, Any]]:
        """
        Returns (best setup or None, cycle meta).

        Meta includes families considered and counts for engine metrics.
        """
        now = time.time()
        meta: Dict[str, Any] = {
            "families_scanned": sorted(self.cfg.series_for_families()),
            "candidates_found": 0,
            "raw_markets": 0,
        }
        rows = self.collect_candidates()
        meta["raw_markets"] = len(rows)
        ranked: List[tuple[float, ScalpSetup]] = []

        for m in rows:
            ticker = str(m.get("ticker") or "").strip()
            if not ticker:
                continue
            try:
                ob = self._orderbook(ticker)
            except Exception as exc:
                logger.debug("orderbook failed %s: %s", ticker, exc)
                continue

            fr: FilterResult = evaluate_scalp_filter(m, ob, cfg=self.cfg, now=now)
            if not fr.ok or fr.family is None or fr.liquidity is None:
                continue

            liq = fr.liquidity
            side = pick_scalp_side(liq)
            ask_c = entry_side_price_cents(liq, side)
            bid_c = mark_price_cents_for_pnl(side, liq)
            if ask_c is None or bid_c is None:
                continue

            vol = _kalshi_market_volume(m)
            sp = (liq.spread_cents_yes() if side == "yes" else liq.spread_cents_no()) or 99.0
            contracts = _contracts_for_deployment(ask_c, self.cfg.deployment_per_trade_usd)
            sc = _score_setup(liq, side, vol)
            setup = ScalpSetup(
                family=fr.family,
                market_ticker=ticker,
                side=side,
                score=sc,
                ask_cents=int(ask_c),
                bid_cents=int(bid_c),
                spread_cents=float(sp),
                contracts=contracts,
                market_row=m,
                orderbook=ob,
                liquidity=liq,
            )
            ranked.append((sc, setup))
            meta["candidates_found"] += 1

        if not ranked:
            return None, meta

        ranked.sort(key=lambda x: -x[0])
        return ranked[0][1], meta


def normalize_scan_report(
    families: List[str],
    approved: bool,
    *,
    skipped_reason: Optional[str] = None,
    setup: Optional[ScalpSetup] = None,
) -> Dict[str, Any]:
    """Structured line for logging / notifier."""
    out: Dict[str, Any] = {
        "families_scanned": families,
        "setup_approved": approved,
        "skipped_reason": skipped_reason,
    }
    if setup:
        out["ticker"] = setup.market_ticker
        out["family"] = setup.family.value
        out["side"] = setup.side
        out["contracts"] = setup.contracts
        out["spread_cents"] = setup.spread_cents
    return out
