"""End-of-day forced execution of top-scored opportunities (backup if daily scan under-trades)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from datetime import datetime
from typing import Sequence, Tuple

from zoneinfo import ZoneInfo

from trading_ai.shark.capital_effective import effective_capital_for_outlet
from trading_ai.shark.execution import run_execution_chain
from trading_ai.shark.hunt_engine import group_markets_by_event, run_hunts_on_market
from trading_ai.shark.models import MarketSnapshot, OpportunityTier, ScoredOpportunity
from trading_ai.shark.scanner import OutletFetcher, scan_markets
from trading_ai.shark.scorer import score_opportunity
from trading_ai.shark.state_store import load_capital

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def _start_of_today_et_ts() -> float:
    now = datetime.now(_ET)
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return sod.timestamp()


def _already_traded_today() -> bool:
    rec = load_capital()
    lt = rec.last_trade_unix
    if lt is None:
        return False
    return float(lt) >= _start_of_today_et_ts()


def run_end_of_day_force_trade(fetchers: Sequence[OutletFetcher]) -> int:
    """
    If enabled and no trades yet today (optional), run one full scan and execute up to 3
    highest-scoring opportunities with score > 0.30 by forcing tier to TIER_A.
    """
    if (os.environ.get("EZRAS_EOD_FORCE_ENABLED", "1") or "").strip().lower() in ("0", "false", "no"):
        logger.info("EOD force trade disabled (EZRAS_EOD_FORCE_ENABLED)")
        return 0
    if (os.environ.get("EZRAS_EOD_FORCE_IF_ZERO", "1") or "").strip().lower() in ("1", "true", "yes"):
        if _already_traded_today():
            logger.info("EOD force skipped: trade already recorded today")
            return 0

    markets = scan_markets(tuple(fetchers), fallback_demo=False)
    if not markets:
        logger.warning("EOD force: no markets from fetchers")
        return 0
    cross = group_markets_by_event(markets)
    now = time.time()
    candidates: list[Tuple[ScoredOpportunity, MarketSnapshot]] = []
    min_score = float(os.environ.get("EZRAS_EOD_FORCE_MIN_SCORE", "0.30") or 0.30)

    for m in markets:
        hunts = run_hunts_on_market(m, cross_context=cross, now=now)
        if not hunts:
            continue
        scored = score_opportunity(m, hunts)
        if scored.score <= min_score:
            continue
        candidates.append((scored, m))

    candidates.sort(key=lambda t: -t[0].score)
    top = candidates[:3]
    if not top:
        logger.info("EOD force: no candidates above score %.3f", min_score)
        return 0

    rec = load_capital()
    book = float(rec.current_capital)
    n_ok = 0
    for scored, m in top:
        outlet = (m.outlet or "").strip() or "unknown"
        if outlet.lower() == "manifold":
            continue
        cap = effective_capital_for_outlet(outlet, book)
        forced = replace(
            scored,
            tier=OpportunityTier.TIER_A,
            tier_sizing_multiplier=1.3,
        )
        try:
            res = run_execution_chain(
                forced,
                capital=cap,
                outlet=outlet,
                strategy_key="shark_default",
            )
            if res.ok:
                n_ok += 1
        except Exception:
            logger.exception("EOD force: chain failed for %s", m.market_id)

    logger.info("EOD force trade: %s trades (attempted %s)", n_ok, len(top))
    return n_ok
