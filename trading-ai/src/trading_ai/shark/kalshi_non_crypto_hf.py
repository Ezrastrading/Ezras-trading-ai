"""Non-crypto Kalshi high-frequency scan — Tier A only, small tickets, capped deployment."""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: str = "true") -> bool:
    import os

    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def _nc_deployed_usd() -> float:
    from trading_ai.shark.state_store import load_positions

    s = 0.0
    for p in load_positions().get("open_positions") or []:
        if str(p.get("outlet") or "").lower() != "kalshi":
            continue
        if str(p.get("strategy_key") or "") != "kalshi_nc_hf":
            continue
        s += float(p.get("notional_usd") or 0)
    return s


def run_kalshi_non_crypto_hf() -> None:
    """Scan non-crypto Kalshi markets in Tier A (5–10m), high probability, place up to N small market buys."""
    import os

    if not _env_truthy("KALSHI_NC_HF_ENABLED", "true"):
        return

    try:
        min_prob = float((os.environ.get("KALSHI_NC_MIN_PROB") or "0.88").strip() or "0.88")
    except ValueError:
        min_prob = 0.88
    try:
        max_per_run = max(1, int(float((os.environ.get("KALSHI_NC_MAX_TRADES_PER_RUN") or "10").strip() or "10")))
    except ValueError:
        max_per_run = 10
    try:
        deploy_cap_pct = max(0.05, min(1.0, float((os.environ.get("KALSHI_NC_DEPLOY_CAP_PCT") or "0.60").strip() or "0.60")))
    except ValueError:
        deploy_cap_pct = 0.60
    try:
        usd_lo = float((os.environ.get("KALSHI_NC_PER_TRADE_MIN_USD") or "1").strip() or "1")
        usd_hi = float((os.environ.get("KALSHI_NC_PER_TRADE_MAX_USD") or "3").strip() or "3")
    except ValueError:
        usd_lo, usd_hi = 1.0, 3.0
    if usd_hi < usd_lo:
        usd_lo, usd_hi = usd_hi, usd_lo

    from trading_ai.shark.capital_effective import effective_capital_for_outlet
    from trading_ai.shark.execution_live import monitor_position
    from trading_ai.shark.kalshi_crypto import kalshi_non_crypto_series_for_active_pool, kalshi_ticker_is_crypto
    from trading_ai.shark.kalshi_expiry_tiers import classify_kalshi_expiry_tier
    from trading_ai.shark.kalshi_limits import (
        count_kalshi_open_positions,
        kalshi_max_open_positions_from_env,
        kalshi_max_position_usd,
        kalshi_min_position_usd,
    )
    from trading_ai.shark.models import HuntType, OpenPosition
    from trading_ai.shark.outlets.kalshi import (
        KalshiClient,
        _kalshi_market_tradeable_core,
        _kalshi_market_volume,
        _kalshi_yes_no_from_market_row,
        _parse_close_timestamp_unix,
    )
    from trading_ai.shark.state_store import load_capital

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        return

    book = load_capital()
    deployable = effective_capital_for_outlet("kalshi", float(book.current_capital))
    cap_usd = deployable * deploy_cap_pct
    already = _nc_deployed_usd()
    headroom = max(0.0, cap_usd - already)
    if headroom < kalshi_min_position_usd():
        logger.debug("kalshi_nc_hf: no headroom under %.0f%% cap (deployed $%.2f / cap $%.2f)", deploy_cap_pct * 100, already, cap_usd)
        return

    now = time.time()
    merged: Dict[str, Dict[str, Any]] = {}
    for ser in kalshi_non_crypto_series_for_active_pool():
        try:
            rows = client.fetch_markets_for_series(ser, limit=120)
        except Exception as exc:
            logger.debug("kalshi_nc_hf series %s: %s", ser, exc)
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("ticker") or "").strip()
            if tid and not kalshi_ticker_is_crypto(tid):
                merged[tid] = row

    candidates: List[Tuple[Dict[str, Any], float, float, str]] = []
    for m in merged.values():
        tid = str(m.get("ticker") or "").strip()
        if not tid or kalshi_ticker_is_crypto(tid):
            continue
        if not _kalshi_market_tradeable_core(m, now):
            continue
        if _kalshi_market_volume(m) < 1.0:
            continue
        end = _parse_close_timestamp_unix(m)
        if end is None:
            continue
        ttr = end - now
        if ttr <= 0:
            continue
        tier = classify_kalshi_expiry_tier(ttr)
        if tier != "A":
            continue
        try:
            row = client.enrich_market_with_detail_and_orderbook(dict(m))
        except Exception:
            row = m
        y, n, _, _ = _kalshi_yes_no_from_market_row(row)
        mx = max(y, n)
        if mx < min_prob:
            continue
        side = "yes" if y >= n else "no"
        px = y if side == "yes" else n
        candidates.append((row, float(end), float(px), side))

    def _mx(row: Dict[str, Any]) -> float:
        y, n, _, _ = _kalshi_yes_no_from_market_row(row)
        return max(y, n)

    candidates.sort(key=lambda x: (-_mx(x[0]), x[1]))
    max_open = kalshi_max_open_positions_from_env()
    open_n = count_kalshi_open_positions()
    slots = max(0, max_open - open_n)
    n_take = min(len(candidates), max_per_run, slots)
    if n_take <= 0:
        logger.info(
            "kalshi_nc_hf: Tier_A min_prob=%.2f — %s candidates, 0 trades (slots/cap)",
            min_prob,
            len(candidates),
        )
        return

    selected = candidates[:n_take]

    pending: List[OpenPosition] = []
    placed = 0
    local_headroom = headroom

    for item in selected:
        if local_headroom < kalshi_min_position_usd():
            break
        m, _end, px, side = item
        ticker = str(m.get("ticker") or "").strip()
        if not ticker:
            continue
        yy, nn, _, _ = _kalshi_yes_no_from_market_row(m)
        mx = max(yy, nn)
        per = random.uniform(usd_lo, usd_hi)
        per = min(per, kalshi_max_position_usd(), local_headroom)
        if per < kalshi_min_position_usd():
            continue
        cnt = max(1, int(per / max(px, 0.01)))
        try:
            res = client.place_order(
                ticker=ticker,
                side=side,
                count=cnt,
            )
        except Exception as exc:
            logger.warning("kalshi_nc_hf order failed %s: %s", ticker, exc)
            continue
        fp = float(res.filled_price or 0.0)
        fs = float(res.filled_size or 0.0)
        if fs <= 0 and res.raw:
            o = res.raw.get("order") if isinstance(res.raw.get("order"), dict) else {}
            fs = float(res.raw.get("filled_count", 0) or o.get("filled_count", 0) or 0)
        if fp <= 0 and px > 0:
            fp = px
        notional = (fp * fs) if fs > 0 else 0.0
        if fs <= 0:
            continue
        pos = OpenPosition(
            position_id=f"nc-hf-{uuid.uuid4().hex[:12]}",
            outlet="kalshi",
            market_id=ticker,
            side=side,
            entry_price=fp if fp > 0 else px,
            shares=fs,
            notional_usd=float(max(notional, kalshi_min_position_usd() * 0.25)),
            order_id=str(res.order_id or ""),
            opened_at=time.time(),
            strategy_key="kalshi_nc_hf",
            hunt_types=[HuntType.NEAR_RESOLUTION_HV.value],
            market_category="kalshi_nc_hf",
            expected_edge=max(0.0, mx - min_prob),
        )
        placed += 1
        local_headroom = max(0.0, local_headroom - float(notional))
        pending.append(pos)

    for pos in pending:
        try:
            monitor_position(pos, save=True)
        except Exception as exc:
            logger.warning("kalshi_nc_hf monitor_position failed %s: %s", pos.market_id, exc)

    logger.info(
        "kalshi_nc_hf: Tier_A min_prob=%.2f — %s candidates, placing %s trades, headroom was $%.2f (cap %.0f%%)",
        min_prob,
        len(candidates),
        placed,
        headroom,
        deploy_cap_pct * 100,
    )
