"""
Bridge: market scan → hunts → score → ``run_execution_chain`` (live venues when not dry-run).

Scanning uses all ``fetchers`` (Kalshi, Polymarket, Manifold, …) for intelligence and price
feeds. **Live US execution is Kalshi only** — Polymarket orders are blocked in
``execution_live.submit_order``; Manifold stays on the mana sandbox path below.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import re
import time
from typing import Dict, Optional, Sequence, Set, Tuple, List

from trading_ai.shark.capital_effective import effective_capital_for_outlet
from trading_ai.shark.execution import _resolve_execute_live, run_execution_chain
from trading_ai.shark.gap_hunter import confirm_pattern, gap_score, scan_for_gaps_stub
from trading_ai.shark.hunt_engine import group_markets_by_event, run_hunts_on_market
from trading_ai.shark.models import HuntType, OpportunityTier
from trading_ai.shark.scanner import OutletFetcher, record_opportunity_for_burst, scan_markets
from trading_ai.shark.scorer import score_opportunity
from trading_ai.shark.state_store import (
    count_kalshi_trades_opened_today_et,
    get_daily_trade_limit_for_capital,
    load_capital,
    load_kalshi_price_history,
    merge_kalshi_prices_from_scan,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50


def _top_per_batch() -> int:
    try:
        return max(5, min(40, int((os.environ.get("SCAN_TOP_PER_BATCH") or "15").strip() or "15")))
    except ValueError:
        return 15


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower())[:120].strip()


def attach_metaculus_reference_prices(markets: Sequence[object]) -> None:
    """Match Kalshi ↔ Metaculus by normalized title (community median as YES reference)."""
    by_title: Dict[str, list] = {}
    for m in markets:
        if (getattr(m, "outlet", None) or "") != "metaculus":
            continue
        k = _norm_title(str(getattr(m, "question_text", None) or getattr(m, "resolution_criteria", "") or ""))
        if not k:
            continue
        by_title.setdefault(k, []).append(m)
    for m in markets:
        if (getattr(m, "outlet", None) or "").lower() != "kalshi":
            continue
        k = _norm_title(str(getattr(m, "question_text", None) or getattr(m, "resolution_criteria", "") or ""))
        u = dict(getattr(m, "underlying_data_if_available", None) or {})
        for mc in by_title.get(k, []):
            my = getattr(mc, "yes_price", None)
            if my is not None:
                u["metaculus_yes_reference"] = float(my)
                break
        m.underlying_data_if_available = u


def attach_poly_reference_prices(markets: Sequence[object]) -> None:
    """Match Kalshi ↔ Polymarket by normalized title so ``KALSHI_CONVERGENCE`` can compare YES prices."""
    by_title: Dict[str, list] = {}
    for m in markets:
        if (getattr(m, "outlet", None) or "") != "polymarket":
            continue
        k = _norm_title(str(getattr(m, "question_text", None) or getattr(m, "resolution_criteria", "") or ""))
        if not k:
            continue
        by_title.setdefault(k, []).append(m)
    for m in markets:
        if (getattr(m, "outlet", None) or "").lower() != "kalshi":
            continue
        k = _norm_title(str(getattr(m, "question_text", None) or getattr(m, "resolution_criteria", "") or ""))
        u = dict(getattr(m, "underlying_data_if_available", None) or {})
        for poly in by_title.get(k, []):
            py = getattr(poly, "yes_price", None)
            if py is not None:
                u["poly_yes_reference"] = float(py)
                break
        m.underlying_data_if_available = u


def scan_fetchers_all() -> List[OutletFetcher]:
    """All default outlets for market intelligence (Kalshi + Polymarket + …)."""
    from trading_ai.shark.outlets import default_fetchers

    return default_fetchers()


def execution_fetchers_kalshi_only() -> List[OutletFetcher]:
    """Subset used when wiring jobs that must only touch Kalshi HTTP; prefer ``scan_fetchers_all()`` for scans."""
    return [f for f in scan_fetchers_all() if "kalshi" in f.outlet_name.lower()]


def _ceo_bump_scan_stats(markets: int, execution_attempts: int) -> None:
    try:
        from trading_ai.shark import ceo_sessions

        ceo_sessions.bump_daily_scan_stats(markets, execution_attempts)
    except Exception:
        pass


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
    hunt_types_filter: Optional[Set[HuntType]] = None,
) -> Tuple[int, int]:
    """
    Fetch markets, run hunts + scoring, execute chain per qualifying market.

    Markets are processed in batches of ``_BATCH_SIZE``; only the top ``_TOP_PER_BATCH``
    by score in each batch are considered for execution (memory bound).

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
        _ceo_bump_scan_stats(0, 0)
        return 0, 0

    attach_poly_reference_prices(markets)
    attach_metaculus_reference_prices(markets)
    price_hist = load_kalshi_price_history()

    if "kalshi" in tag.lower():
        kalshi_sample = [m for m in markets if (getattr(m, "outlet", None) or "").lower() == "kalshi"][:10]
        for m in kalshi_sample:
            q = str(getattr(m, "question_text", None) or getattr(m, "resolution_criteria", "") or "")[:80]
            logger.info(
                "Kalshi market sample: id=%s yes=%s no=%s q=%s",
                str(getattr(m, "market_id", ""))[:48],
                getattr(m, "yes_price", None),
                getattr(m, "no_price", None),
                q,
            )

    n_m = len(markets)
    yes_none = sum(1 for m in markets if getattr(m, "yes_price", None) is None)
    yes_float = sum(1 for m in markets if getattr(m, "yes_price", None) is not None)
    end_none = sum(1 for m in markets if getattr(m, "end_date_seconds", None) is None)
    sample_sums = [round(float(m.yes_price) + float(m.no_price), 4) for m in markets[: min(n_m, 20)]]
    logger.info(
        "hunt_diag tag=%s markets=%s yes_price_is_none=%s yes_price_present=%s "
        "end_date_seconds_is_none=%s yes_plus_no_sum_sample_first20=%s",
        tag,
        n_m,
        yes_none,
        yes_float,
        end_none,
        sample_sums,
    )
    for m in markets[:5]:
        logger.info(
            "Sample market: id=%s yes=%s no=%s end=%s vol=%s",
            str(m.market_id)[:20],
            m.yes_price,
            m.no_price,
            getattr(m, "end_date_seconds", None),
            getattr(m, "volume_24h", None),
        )

    logger.info("Processing %s markets through hunt engine (batched)", len(markets))
    cross = group_markets_by_event(markets)
    now = time.time()
    rec = load_capital()
    capital = float(rec.current_capital)
    attempts = 0
    hunt_nonempty = 0

    for i in range(0, len(markets), _BATCH_SIZE):
        batch = markets[i : i + _BATCH_SIZE]
        batch_rows: list[tuple] = []
        for j, m in enumerate(batch):
            global_idx = i + j
            htf = hunt_types_filter
            if (m.outlet or "").strip().lower() == "manifold":
                from trading_ai.shark.mana_sandbox import mana_effective_hunt_filter

                htf = mana_effective_hunt_filter(hunt_types_filter)
            hunts = run_hunts_on_market(
                m,
                cross_context=cross,
                now=now,
                hunt_types_filter=htf,
                hunt_diag_index=global_idx if global_idx < 10 else None,
                price_history=price_hist,
            )
            if not hunts:
                continue
            hunt_nonempty += 1
            batch_rows.append((score_opportunity(m, hunts), m))
        batch_rows.sort(key=lambda t: -t[0].score)
        for scored, m in batch_rows[: _top_per_batch()]:
            if scored.tier == OpportunityTier.BELOW_THRESHOLD:
                continue
            record_opportunity_for_burst(now)
            attempts += 1
            outlet = (m.outlet or "").strip() or "unknown"
            if outlet.lower() == "manifold":
                from trading_ai.shark.capital_phase import detect_phase, phase_params
                from trading_ai.shark.executor import build_execution_intent
                from trading_ai.shark.mana_sandbox import (
                    MANA_RECOVERY_MAX_STAKE_FRACTION,
                    MANA_RECOVERY_MIN_CERTAINTY,
                    execute_mana_trade,
                    is_btc_five_min_market,
                    is_mana_recovery_mode,
                    load_mana_state,
                    mana_effective_min_edge_for_intent,
                )
                from trading_ai.shark.risk_context import build_risk_context

                intent = None
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
                if is_mana_recovery_mode():
                    mxp = max(float(m.yes_price), float(m.no_price))
                    if mxp < MANA_RECOVERY_MIN_CERTAINTY:
                        logger.info(
                            "%s mana recovery: skip (certainty %.3f < %.2f) %s",
                            tag,
                            mxp,
                            MANA_RECOVERY_MIN_CERTAINTY,
                            m.market_id,
                        )
                        continue
                    if not is_btc_five_min_market(m):
                        logger.info("%s mana recovery: skip (not BTC 5m) %s", tag, m.market_id)
                        continue
                hunt_labels = [h.hunt_type.value for h in scored.hunts]
                min_e = mana_effective_min_edge_for_intent(risk.effective_min_edge, hunt_labels)
                intent = build_execution_intent(
                    scored,
                    capital=mana_cap,
                    outlet="manifold",
                    gap_exploitation_mode=False,
                    current_gap_exposure_fraction=0.0,
                    min_edge_effective=min_e,
                    risk_position_multiplier=risk.position_size_multiplier,
                    market_category=m.market_category,
                    is_mana=True,
                    current_drawdown_pct=risk.drawdown_from_peak,
                )
                if intent is not None:
                    intent.meta["question_text"] = (
                        m.question_text or m.resolution_criteria or str(m.market_id)
                    )[:500]
                    if is_mana_recovery_mode():
                        cap_st = MANA_RECOVERY_MAX_STAKE_FRACTION
                        if intent.stake_fraction_of_capital > cap_st:
                            intent.stake_fraction_of_capital = cap_st
                            intent.notional_usd = max(0.0, mana_cap * cap_st)
                            px = max(intent.expected_price, 1e-6)
                            intent.shares = max(1, int(intent.notional_usd / px))
                    from trading_ai.shark.claude_eval import apply_claude_evaluator_gate
                    from trading_ai.shark.models import HuntType

                    skip_claude = HuntType.NEAR_RESOLUTION_HV in (intent.hunt_types or []) and float(
                        intent.estimated_win_probability or 0
                    ) >= 0.949
                    if skip_claude:
                        ok_m, halt_m = True, ""
                    else:
                        ok_m, halt_m = apply_claude_evaluator_gate(scored, intent, capital=mana_cap)
                    if not ok_m:
                        logger.info("%s mana_sandbox: Claude gate blocked (%s) %s", tag, halt_m, m.market_id)
                    else:
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

            if outlet.lower() == "metaculus":
                continue
            if outlet.lower() != "kalshi":
                logger.info(
                    "%s: live execution skipped (Kalshi only) outlet=%s market=%s",
                    tag,
                    outlet,
                    m.market_id,
                )
                continue

            if (os.environ.get("EZRAS_KALSHI_DAILY_CAP_DISABLED") or "").strip().lower() in ("1", "true", "yes"):
                n_k_today, daily_cap = 0, 10**9
            else:
                daily_cap = get_daily_trade_limit_for_capital(capital)
                n_k_today = count_kalshi_trades_opened_today_et()
            if n_k_today >= daily_cap:
                logger.info(
                    "%s: Kalshi daily trade limit reached %s/%s — skipping execution",
                    tag,
                    n_k_today,
                    daily_cap,
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

        del batch_rows
        del batch
        gc.collect()

    logger.info(
        "Hunt results: %s markets with qualifying hunts",
        hunt_nonempty,
    )
    merge_kalshi_prices_from_scan(markets)
    _touch_last_scan_unix(time.time())
    _post_scan_balance_sync()
    _ceo_bump_scan_stats(len(markets), attempts)
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
