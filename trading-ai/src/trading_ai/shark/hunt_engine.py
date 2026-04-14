"""Six hunt types — run on each market; cross-market hunts need batch context."""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from trading_ai.shark.crypto_polymarket_hunts import (
    append_polymarket_strategy_hunts,
    hunt_diagnostic_context,
    run_filtered_polymarket_hunts,
)
from trading_ai.shark.models import HuntSignal, HuntType, MarketSnapshot

# Tunable liquidity floors (quote currency units)
_MIN_VOLUME_24H_DEFAULT = 500.0
_HUNT6_MIN_VOLUME_24H = 300.0
_DEPTH_MIN = 50.0
# Full hunt suite (1–7 + cross) only when 24h volume exceeds this; else Hunts 1–2 only (memory/CPU).
_FULL_HUNT_MIN_VOLUME_24H = 1000.0


def _volume_ok(vol: float, minimum: float = _MIN_VOLUME_24H_DEFAULT) -> bool:
    return vol >= minimum


def infer_true_probability(m: MarketSnapshot) -> float:
    """Best-effort probability from criteria + underlying; capped 0.01-0.99."""
    u = m.underlying_data_if_available or {}
    if "model_prob" in u:
        return max(0.01, min(0.99, float(u["model_prob"])))
    if "true_prob" in u:
        return max(0.01, min(0.99, float(u["true_prob"])))
    # crude: if criteria mentions '>90%' style — else 0.5
    crit = (m.resolution_criteria or "").lower()
    if "near certainty" in crit or ">90%" in crit or ">=0.9" in crit:
        return 0.92
    return float(u.get("implied_prob", 0.5))


def hunt_dead_market_convergence(m: MarketSnapshot) -> Optional[HuntSignal]:
    if m.time_to_resolution_seconds >= 4 * 3600:
        return None
    p_true = infer_true_probability(m)
    favored_yes = p_true >= 0.9
    favored_no = (1 - p_true) >= 0.9
    if not favored_yes and not favored_no:
        return None
    price = m.yes_price if favored_yes else m.no_price
    if price >= 0.88:
        return None
    if not _volume_ok(m.volume_24h):
        return None
    edge = abs(p_true - price)  # simplified edge after fees assumed folded into threshold
    if edge < 0.02:
        return None
    conf = min(1.0, edge / 0.15)
    return HuntSignal(
        HuntType.DEAD_MARKET_CONVERGENCE,
        edge_after_fees=edge,
        confidence=conf,
        details={"p_true": p_true, "side": "yes" if favored_yes else "no"},
    )


def hunt_structural_arbitrage(m: MarketSnapshot) -> Optional[HuntSignal]:
    s = m.yes_price + m.no_price
    if s >= 0.97:
        return None
    if m.time_to_resolution_seconds <= 30 * 60:
        return None
    if not (_volume_ok(m.volume_24h / 2) and _volume_ok(m.volume_24h / 2)):
        return None
    edge = 1.0 - s  # before fees; assume fees embedded in 0.97 threshold
    if edge < 0.01:
        return None
    return HuntSignal(
        HuntType.STRUCTURAL_ARBITRAGE,
        edge_after_fees=edge,
        confidence=min(1.0, edge / 0.1),
        details={"sum": s},
    )


def hunt_cross_platform_mispricing(markets: Sequence[MarketSnapshot]) -> Dict[str, HuntSignal]:
    """Call with all markets sharing canonical_event_key."""
    out: Dict[str, HuntSignal] = {}
    if len(markets) < 2:
        return out
    outlets = {m.outlet for m in markets}
    if len(outlets) < 2:
        return out
    prices_yes = [m.yes_price for m in markets]
    diff = max(prices_yes) - min(prices_yes)
    if diff <= 0.04:
        return out
    if any(not _volume_ok(m.volume_24h) for m in markets):
        return out
    edge = diff * 0.5  # conservative after fees
    if edge < 0.02:
        return out
    sig = HuntSignal(
        HuntType.CROSS_PLATFORM_MISPRICING,
        edge_after_fees=edge,
        confidence=min(1.0, edge / 0.2),
        details={"yes_spread": diff, "outlets": sorted(outlets)},
    )
    for m in markets:
        out[m.market_id] = sig
    return out


def hunt_statistical_window(m: MarketSnapshot) -> Optional[HuntSignal]:
    if m.historical_sample_count < 30 or m.historical_yes_rate is None:
        return None
    u = m.underlying_data_if_available or {}
    if u.get("major_event_within_30_min"):
        return None
    hr = m.historical_yes_rate
    if not (hr > 0.65 or hr < 0.35):
        return None
    dev = abs(m.yes_price - hr)
    if dev <= 0.12:
        return None
    if m.scheduled_event_in_seconds is not None and m.scheduled_event_in_seconds < 30 * 60:
        return None
    edge = dev * 0.6
    if edge < 0.03:
        return None
    return HuntSignal(
        HuntType.STATISTICAL_WINDOW,
        edge_after_fees=edge,
        confidence=min(1.0, dev),
        details={"historical_yes_rate": hr},
    )


def hunt_near_zero_accumulation(
    m: MarketSnapshot,
    *,
    macro_feed: Optional[Dict[str, Any]] = None,
) -> Optional[HuntSignal]:
    """
    Hunt 6 — buy YES at 2–12¢ when structural base rate supports payoff; long-dated only.
    Uses optional macro feed to nudge economics markets (non-blocking if absent).
    """
    u = m.underlying_data_if_available or {}
    if u.get("negative_catalyst_48h"):
        return None
    y = m.yes_price
    if not (0.02 <= y <= 0.12):
        return None
    if m.time_to_resolution_seconds <= 7 * 24 * 3600:
        return None
    if not _volume_ok(m.volume_24h, minimum=_HUNT6_MIN_VOLUME_24H):
        return None
    base = m.historical_yes_rate
    if base is None:
        base = infer_true_probability(m)
    if macro_feed:
        from trading_ai.shark import data_feeds as _df

        base = _df.enrich_hunt_base_rate(base, m.market_category, macro_feed)
    if base <= 0.20:
        return None
    ev = base - m.yes_price
    if ev < 0.03:
        return None
    tracked = bool(u.get("tracked_wallet_match", False))
    conf = 0.72 if tracked else 0.58
    return HuntSignal(
        HuntType.NEAR_ZERO_ACCUMULATION,
        edge_after_fees=ev,
        confidence=conf,
        details={
            "base_rate": base,
            "side": "yes",
            "tracked_wallet_match": tracked,
            "hunt": "near_zero_accumulation",
        },
    )


def hunt_options_binary(m: MarketSnapshot) -> Optional[HuntSignal]:
    """Hunt 7 — tagged options-style binary markets (``options_binary`` in meta or category)."""
    u = m.underlying_data_if_available or {}
    if m.market_category != "options_binary" and not u.get("options_binary"):
        return None
    edge = float(u.get("options_edge", 0.0) or 0.0)
    if edge < 0.05:
        return None
    if not _volume_ok(m.volume_24h):
        return None
    side = str(u.get("side", "yes"))
    return HuntSignal(
        HuntType.OPTIONS_BINARY,
        edge_after_fees=edge,
        confidence=0.55,
        details={"side": side, "kind": "options_binary"},
    )


def hunt_liquidity_imbalance_fade(m: MarketSnapshot, now: Optional[float] = None) -> Optional[HuntSignal]:
    now = now or time.time()
    dy, dn = m.order_book_bid_depth_yes, m.order_book_bid_depth_no
    if dy <= 0 or dn <= 0:
        return None
    ratio = max(dy / dn, dn / dy)
    if ratio < 3.0:
        return None
    if m.imbalance_since_unix is None or (now - m.imbalance_since_unix) < 10 * 60:
        return None
    if m.time_to_resolution_seconds <= 3600:
        return None
    edge = 0.02 + 0.015 * min(2.0, math.log(ratio) / math.log(3.0))
    if edge < 0.02:
        return None
    return HuntSignal(
        HuntType.LIQUIDITY_IMBALANCE_FADE,
        edge_after_fees=edge,
        confidence=0.55,
        details={"depth_ratio": ratio},
    )


def run_hunts_on_market(
    m: MarketSnapshot,
    *,
    cross_context: Optional[Dict[str, Sequence[MarketSnapshot]]] = None,
    now: Optional[float] = None,
    macro_feed: Optional[Dict[str, Any]] = None,
    hunt_types_filter: Optional[Set[HuntType]] = None,
    hunt_diag_index: Optional[int] = None,
) -> List[HuntSignal]:
    """Run Hunts 1–2 only when volume is low; full suite (3–7 + cross) when volume > $1k / 24h.

    When ``hunt_types_filter`` is set (e.g. 30s crypto scan), only those fast hunts run;
    Polymarket + Kalshi for arb / near-resolution / volume (crypto scalp + order book are Polymarket-only).

    ``hunt_diag_index`` 0..9 enables INFO-level diagnostic lines in ``hunt_near_resolution`` /
    ``hunt_pure_arbitrage`` for the first ten markets of a scan cycle.
    """
    with hunt_diagnostic_context(hunt_diag_index):
        if hunt_types_filter:
            return run_filtered_polymarket_hunts(m, hunt_types_filter, now=now)
        sigs: List[HuntSignal] = []
        if m.volume_24h <= _FULL_HUNT_MIN_VOLUME_24H:
            for fn in (hunt_dead_market_convergence, hunt_structural_arbitrage):
                r = fn(m)
                if r:
                    sigs.append(r)
            if (m.outlet or "").lower() in ("polymarket", "kalshi"):
                sigs.extend(append_polymarket_strategy_hunts(m, now=now))
            return sigs
        for fn in (hunt_dead_market_convergence, hunt_structural_arbitrage, hunt_statistical_window, hunt_options_binary):
            r = fn(m)
            if r:
                sigs.append(r)
        nz = hunt_near_zero_accumulation(m, macro_feed=macro_feed)
        if nz:
            sigs.append(nz)
        liq = hunt_liquidity_imbalance_fade(m, now=now)
        if liq:
            sigs.append(liq)
        if cross_context and m.canonical_event_key:
            group = cross_context.get(m.canonical_event_key, [])
            xm = hunt_cross_platform_mispricing(group)
            if m.market_id in xm:
                sigs.append(xm[m.market_id])
        # Hunts 8–12 (crypto scalp, arb, near resolution, order book, volume spike) — all CLOB/Kalshi categories, no whitelist.
        if (m.outlet or "").lower() in ("polymarket", "kalshi"):
            sigs.extend(append_polymarket_strategy_hunts(m, now=now))
        return sigs


def group_markets_by_event(markets: Iterable[MarketSnapshot]) -> Dict[str, List[MarketSnapshot]]:
    d: Dict[str, List[MarketSnapshot]] = {}
    for m in markets:
        k = m.canonical_event_key
        if not k:
            continue
        d.setdefault(k, []).append(m)
    return d
