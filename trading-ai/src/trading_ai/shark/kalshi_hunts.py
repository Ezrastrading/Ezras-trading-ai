"""Kalshi-specific hunt signals — near-close, Polymarket divergence, momentum."""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from trading_ai.shark.models import HuntSignal, HuntType, MarketSnapshot

logger = logging.getLogger(__name__)

_HV_TIERS: Tuple[Tuple[float, float, str], ...] = (
    (0.97, 0.75, "T1"),
    (0.93, 0.50, "T2"),
    (0.90, 0.30, "T3"),
)


def _hv_metaculus_agrees_yes(m: MarketSnapshot, yes: float) -> bool:
    u = m.underlying_data_if_available or {}
    meta = u.get("metaculus_yes_reference")
    if meta is None:
        return False
    try:
        my = float(meta)
    except (TypeError, ValueError):
        return False
    return yes >= 0.90 and my >= 0.90 and abs(yes - my) <= 0.10


def _hv_metaculus_agrees_no(m: MarketSnapshot, no: float) -> bool:
    u = m.underlying_data_if_available or {}
    meta = u.get("metaculus_yes_reference")
    if meta is None:
        return False
    try:
        my = float(meta)
        m_no = 1.0 - my
    except (TypeError, ValueError):
        return False
    return no >= 0.90 and m_no >= 0.90 and abs(no - m_no) <= 0.10


def hunt_near_resolution_hv(m: MarketSnapshot) -> Optional[HuntSignal]:
    """
    High-confidence near-resolution / live sports: YES or NO side at 90%+.
    Tiers: 97%+ → 75% stake fraction, 93–97% → 50%, 90–93% → 30% (of capital subject to executor caps).
    Metaculus agreement (both venues 90%+) scales stake fraction by 1.25× capped at 0.80.
    """
    if (m.outlet or "").lower() not in ("kalshi", "manifold"):
        return None
    yes = m.yes_price
    no = m.no_price
    if yes is None or no is None:
        return None
    fy = float(yes)
    fn = float(no)

    for thr, frac, tier in _HV_TIERS:
        if fy >= thr:
            boost = 1.25 if _hv_metaculus_agrees_yes(m, fy) else 1.0
            stake = min(0.80, frac * boost)
            edge = max(1.0 - fy, 1e-6)
            return HuntSignal(
                HuntType.NEAR_RESOLUTION_HV,
                edge_after_fees=edge,
                confidence=fy,
                details={
                    "side": "yes",
                    "stake_fraction": stake,
                    "tier": tier,
                    "reasoning": f"{tier} YES={fy:.2f} stake={stake:.0%} edge={edge:.3f}",
                    "metaculus_agreement_boost": boost > 1.0,
                },
            )
    for thr, frac, tier in _HV_TIERS:
        if fn >= thr:
            boost = 1.25 if _hv_metaculus_agrees_no(m, fn) else 1.0
            stake = min(0.80, frac * boost)
            edge = max(1.0 - fn, 1e-6)
            return HuntSignal(
                HuntType.NEAR_RESOLUTION_HV,
                edge_after_fees=edge,
                confidence=fn,
                details={
                    "side": "no",
                    "stake_fraction": stake,
                    "tier": tier,
                    "reasoning": f"{tier} NO={fn:.2f} stake={stake:.0%} edge={edge:.3f}",
                    "metaculus_agreement_boost": boost > 1.0,
                },
            )
    return None


def hunt_kalshi_near_close(m: MarketSnapshot) -> Optional[HuntSignal]:
    if (m.outlet or "").lower() != "kalshi":
        return None
    end = getattr(m, "end_date_seconds", None)
    if end is None:
        end = getattr(m, "end_timestamp_unix", None)
    if end is None:
        return None
    hours_left = (float(end) - time.time()) / 3600.0
    # Within 24h of resolution (was 4h); skip expired or far-dated.
    if hours_left > 24.0 or hours_left < 0:
        return None
    yes = m.yes_price
    no = m.no_price
    if yes is None or no is None:
        return None
    fy = float(yes)
    fn = float(no)
    # YES clearly favored (>=70%) or weak (<=30% → bet NO).
    if fy >= 0.70:
        edge = max(fy - 0.60, 1e-6)
        return HuntSignal(
            HuntType.KALSHI_NEAR_CLOSE,
            edge_after_fees=edge,
            confidence=0.75,
            details={
                "side": "yes",
                "hours_left": hours_left,
                "reasoning": f"Kalshi closing in {hours_left:.1f}h YES={fy:.2f}",
            },
        )
    if fy <= 0.30:
        edge = max(fn - 0.60, 1e-6)
        return HuntSignal(
            HuntType.KALSHI_NEAR_CLOSE,
            edge_after_fees=edge,
            confidence=0.75,
            details={
                "side": "no",
                "hours_left": hours_left,
                "reasoning": f"Kalshi closing in {hours_left:.1f}h YES={fy:.2f} NO={fn:.2f}",
            },
        )
    return None


def hunt_kalshi_metaculus_divergence(m: MarketSnapshot) -> Optional[HuntSignal]:
    if (m.outlet or "").lower() != "kalshi":
        return None
    u = m.underlying_data_if_available or {}
    meta = u.get("metaculus_yes_reference")
    if meta is None:
        return None
    ky = m.yes_price
    if ky is None:
        return None
    my = float(meta)
    divergence = abs(float(ky) - my)
    if divergence < 0.06:
        return None
    side = "yes" if float(ky) < my else "no"
    return HuntSignal(
        HuntType.KALSHI_METACULUS_DIVERGE,
        edge_after_fees=divergence,
        confidence=0.68,
        details={
            "side": side,
            "metaculus_yes": my,
            "kalshi_yes": float(ky),
            "reasoning": f"Kalshi YES={float(ky):.2f} vs Metaculus={my:.2f} gap={divergence:.2f}",
        },
    )


def hunt_kalshi_metaculus_agreement(m: MarketSnapshot) -> Optional[HuntSignal]:
    if (m.outlet or "").lower() != "kalshi":
        return None
    u = m.underlying_data_if_available or {}
    meta = u.get("metaculus_yes_reference")
    if meta is None:
        return None
    ky = float(m.yes_price)
    my = float(meta)
    if abs(ky - my) > 0.04:
        return None
    if not (ky >= 0.55 or ky <= 0.45):
        return None
    edge = 0.025 + abs(ky - 0.5) * 0.05
    return HuntSignal(
        HuntType.KALSHI_METACULUS_AGREE,
        edge_after_fees=edge,
        confidence=0.72,
        details={
            "side": "yes" if ky >= 0.5 else "no",
            "metaculus_yes": my,
            "kalshi_yes": ky,
            "reasoning": f"Kalshi and Metaculus aligned (~{ky:.2f})",
        },
    )


def hunt_kalshi_polymarket_divergence(m: MarketSnapshot) -> Optional[HuntSignal]:
    if (m.outlet or "").lower() != "kalshi":
        return None
    u = m.underlying_data_if_available or {}
    poly_price = u.get("poly_yes_reference")
    if poly_price is None:
        return None
    kalshi_yes = m.yes_price
    if kalshi_yes is None:
        return None
    poly_yes = float(poly_price)
    divergence = abs(float(kalshi_yes) - poly_yes)
    if divergence < 0.05:
        return None
    if float(kalshi_yes) < poly_yes - 0.05:
        return HuntSignal(
            HuntType.KALSHI_CONVERGENCE,
            edge_after_fees=divergence,
            confidence=0.70,
            details={
                "side": "yes",
                "poly_yes": poly_yes,
                "kalshi_yes": float(kalshi_yes),
                "reasoning": f"Kalshi YES={kalshi_yes:.2f} vs Poly={poly_yes:.2f} divergence={divergence:.2f}",
            },
        )
    return None


def hunt_kalshi_momentum(
    m: MarketSnapshot,
    *,
    price_history: Optional[Dict[str, List[float]]] = None,
) -> Optional[HuntSignal]:
    if (m.outlet or "").lower() != "kalshi":
        return None
    ph = price_history or {}
    history = ph.get(m.market_id, [])
    if len(history) < 3:
        return None
    prev_price = float(history[-3])
    curr = m.yes_price
    if curr is None:
        return None
    curr_price = float(curr)
    move = curr_price - prev_price
    if abs(move) < 0.05:
        return None
    side = "yes" if move > 0 else "no"
    edge = max(abs(move) * 0.5, 1e-6)
    return HuntSignal(
        HuntType.KALSHI_MOMENTUM,
        edge_after_fees=edge,
        confidence=0.65,
        details={
            "side": side,
            "prev_yes": prev_price,
            "curr_yes": curr_price,
            "reasoning": f"Momentum: {prev_price:.2f} → {curr_price:.2f} move={move:+.2f}",
        },
    )
