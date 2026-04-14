"""Kalshi-specific hunt signals — near-close, Polymarket divergence, momentum."""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from trading_ai.shark.models import HuntSignal, HuntType, MarketSnapshot

logger = logging.getLogger(__name__)


def hunt_kalshi_near_close(m: MarketSnapshot) -> Optional[HuntSignal]:
    if (m.outlet or "").lower() != "kalshi":
        return None
    end = getattr(m, "end_date_seconds", None)
    if end is None:
        end = getattr(m, "end_timestamp_unix", None)
    if end is None:
        return None
    hours_left = (float(end) - time.time()) / 3600.0
    if hours_left > 4.0 or hours_left < 0:
        return None
    yes = m.yes_price
    no = m.no_price
    if yes is None or no is None:
        return None
    if float(yes) >= 0.85:
        edge = max(float(yes) - 0.80, 1e-6)
        return HuntSignal(
            HuntType.KALSHI_NEAR_CLOSE,
            edge_after_fees=edge,
            confidence=0.80,
            details={
                "side": "yes",
                "hours_left": hours_left,
                "reasoning": f"Kalshi closing in {hours_left:.1f}h YES={yes:.2f}",
            },
        )
    if float(no) >= 0.85:
        edge = max(float(no) - 0.80, 1e-6)
        return HuntSignal(
            HuntType.KALSHI_NEAR_CLOSE,
            edge_after_fees=edge,
            confidence=0.80,
            details={
                "side": "no",
                "hours_left": hours_left,
                "reasoning": f"Kalshi closing in {hours_left:.1f}h NO={no:.2f}",
            },
        )
    return None


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
