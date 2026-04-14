"""
Bridge: market scan → hunts → score → ``run_execution_chain`` (live venues when not dry-run).
Manifold routes to mana sandbox (silent learning); Kalshi/Polymarket use real chain.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Sequence, Tuple

from trading_ai.shark.capital_effective import effective_capital_for_outlet
from trading_ai.shark.execution import _resolve_execute_live, run_execution_chain
from trading_ai.shark.gap_hunter import confirm_pattern, gap_score, scan_for_gaps_stub
from trading_ai.shark.hunt_engine import group_markets_by_event, run_hunts_on_market
from trading_ai.shark.models import MarketSnapshot, OpportunityTier
from trading_ai.shark.scanner import OutletFetcher, record_opportunity_for_burst, scan_markets
from trading_ai.shark.scorer import score_opportunity
from trading_ai.shark.state_store import load_capital

logger = logging.getLogger(__name__)


def _post_scan_balance_sync() -> None:
    try:
        from trading_ai.shark.balance_sync import sync_all_platforms

        sync_all_platforms()
    except Exception as exc:
        logger.warning("balance sync after scan failed (non-blocking): %s", exc)


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
    logger.info(
        f"scan cycle: execute_live="
        f"{_resolve_execute_live(None)}"
    )
    markets = scan_markets(tuple(fetchers), fallback_demo=False)
    if not markets:
        _post_scan_balance_sync()
        return 0, 0

    logger.info("Processing %s markets through hunt engine", len(markets))
    cross = group_markets_by_event(markets)
    now = time.time()
    rec = load_capital()
    capital = float(rec.current_capital)
    attempts = 0
    hunt_results: list = []
    for m in markets:
        hunts = run_hunts_on_market(m, cross_context=cross, now=now)
        hunt_results.append(hunts)
        if not hunts:
            continue
        scored = score_opportunity(m, hunts)
        if scored.tier == OpportunityTier.BELOW_THRESHOLD:
            continue
        record_opportunity_for_burst(now)
        attempts += 1
        outlet = (m.outlet or "").strip() or "unknown"
        if outlet.lower() == "manifold":
            from trading_ai.shark.capital_phase import detect_phase, phase_params
            from trading_ai.shark.executor import build_execution_intent
            from trading_ai.shark.mana_sandbox import execute_mana_trade, load_mana_state
            from trading_ai.shark.risk_context import build_risk_context

            ms = load_mana_state()
            mana_cap = float(ms.get("mana_balance", 0) or 0)
            mana_peak = float(ms.get("mana_peak", mana_cap) or mana_cap)
            phase = detect_phase(mana_cap)
            pp = phase_params(phase)
            risk = build_risk_context(
                current_capital=mana_cap,
                peak_capital=mana_peak,
                base_min_edge=pp.min_edge,
                last_trade_unix=rec.last_trade_unix,
                now_unix=now,
            )
            intent = build_execution_intent(
                scored,
                capital=mana_cap,
                outlet="manifold",
                gap_exploitation_mode=False,
                current_gap_exposure_fraction=0.0,
                min_edge_effective=risk.effective_min_edge,
                risk_position_multiplier=risk.position_size_multiplier,
                market_category=m.market_category,
                is_mana=True,
                current_drawdown_pct=risk.drawdown_from_peak,
            )
            if intent is not None:
                try:
                    execute_mana_trade(intent, scored=scored)
                except Exception:
                    logger.exception("%s: mana sandbox failed for %s", tag, m.market_id)
            logger.info(
                "%s mana_sandbox: market=%s ok=intent=%s",
                tag,
                m.market_id,
                intent is not None,
            )
            continue

        cap_for = effective_capital_for_outlet(outlet, capital)
        try:
            res = run_execution_chain(
                scored,
                capital=cap_for,
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

    logger.info(
        "Hunt results: %s markets with qualifying hunts",
        len([h for h in hunt_results if h]),
    )
    _touch_last_scan_unix(time.time())
    _post_scan_balance_sync()
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
