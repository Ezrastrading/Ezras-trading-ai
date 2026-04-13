"""
Bridge: market scan → hunts → score → ``run_execution_chain`` (live venues when not dry-run).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Sequence, Tuple

from trading_ai.shark.execution import run_execution_chain
from trading_ai.shark.gap_hunter import confirm_pattern, gap_score, scan_for_gaps_stub
from trading_ai.shark.hunt_engine import group_markets_by_event, run_hunts_on_market
from trading_ai.shark.models import MarketSnapshot, OpportunityTier
from trading_ai.shark.scanner import OutletFetcher, record_opportunity_for_burst, scan_markets
from trading_ai.shark.scorer import score_opportunity
from trading_ai.shark.state_store import load_capital

logger = logging.getLogger(__name__)


def run_scan_execution_cycle(
    fetchers: Sequence[OutletFetcher],
    *,
    tag: str = "scan",
) -> Tuple[int, int]:
    """
    Fetch markets, run hunts + scoring, execute chain per qualifying market.

    Returns ``(markets_seen, execution_attempts)`` where attempts counts chains entered
    (tier above threshold), not necessarily filled.
    """
    markets = scan_markets(tuple(fetchers), fallback_demo=False)
    if not markets:
        return 0, 0

    cross = group_markets_by_event(markets)
    now = time.time()
    rec = load_capital()
    capital = float(rec.current_capital)
    attempts = 0

    for m in markets:
        hunts = run_hunts_on_market(m, cross_context=cross, now=now)
        if not hunts:
            continue
        scored = score_opportunity(m, hunts)
        if scored.tier == OpportunityTier.BELOW_THRESHOLD:
            continue
        record_opportunity_for_burst(now)
        attempts += 1
        outlet = (m.outlet or "").strip() or "unknown"
        try:
            res = run_execution_chain(
                scored,
                capital=capital,
                outlet=outlet,
                strategy_key="shark_default",
            )
            logger.info(
                "%s chain: market=%s outlet=%s ok=%s halted=%s",
                tag,
                m.market_id,
                outlet,
                res.ok,
                res.halted_at,
            )
        except Exception:
            logger.exception("%s: run_execution_chain failed for %s", tag, m.market_id)

    _touch_last_scan_unix(time.time())
    return len(markets), attempts


def _touch_last_scan_unix(ts: float) -> None:
    from trading_ai.governance.storage_architecture import shark_state_path

    p = shark_state_path("last_scan.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"last_unix": ts}, indent=2), encoding="utf-8")


def run_gap_confirmed_hook() -> None:
    """
    When gap observations confirm a structural pattern, log and prepare for future
    market-bound execution. ``scan_for_gaps_stub`` is empty until real monitors are wired.
    """
    observations = scan_for_gaps_stub()
    if len(observations) < 5:
        return
    if not confirm_pattern(observations, min_obs=5):
        return
    sc = gap_score(observations)
    logger.info(
        "gap pattern confirmed (score=%.4f) — bind to MarketSnapshot in gap pipeline to execute",
        sc,
    )
