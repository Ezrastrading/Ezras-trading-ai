"""
Cross-avenue capital routing — shift weight from weak to strong venues with a floor.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SHIFT = 0.10
DEFAULT_MIN_ALLOCATION = 0.05


def _normalize(weights: Dict[str, float]) -> Dict[str, float]:
    s = sum(max(0.0, float(v)) for v in weights.values())
    if s <= 0:
        n = max(len(weights), 1)
        return {k: 1.0 / n for k in weights}
    return {k: max(0.0, float(v)) / s for k, v in weights.items()}


def shift_capital(
    pnls: Dict[str, float],
    *,
    current_weights: Optional[Dict[str, float]] = None,
    shift_fraction: float = DEFAULT_SHIFT,
    min_allocation: float = DEFAULT_MIN_ALLOCATION,
) -> Dict[str, float]:
    """
    Move ``shift_fraction`` of total weight from non-best venue(s) toward the best PnL avenue.

    Each venue keeps at least ``min_allocation`` when possible.
    """
    if not pnls:
        return {}

    avenues = list(pnls.keys())
    n = len(avenues)
    if n < 2:
        w = current_weights or {a: 1.0 / max(n, 1) for a in avenues}
        return _normalize({k: float(w.get(k, 0.0)) for k in avenues})

    fr = dict(current_weights) if current_weights else {a: 1.0 / n for a in avenues}
    for a in avenues:
        fr.setdefault(a, 0.0)
    fr = _normalize({k: float(fr.get(k, 0.0)) for k in avenues})

    scores = {str(k): float(v) for k, v in pnls.items()}
    best = max(scores, key=lambda k: scores[k])
    others = [a for a in avenues if a != best]

    total_shift = 0.0
    for o in others:
        take = min(
            shift_fraction / max(len(others), 1),
            max(0.0, fr.get(o, 0.0) - min_allocation),
        )
        take = max(0.0, take)
        fr[o] = fr.get(o, 0.0) - take
        total_shift += take
    fr[best] = fr.get(best, 0.0) + total_shift

    out = _normalize(fr)
    worst = min(scores, key=lambda k: scores[k])
    logger.info(
        "capital_intelligence: shift=%.2f best=%s worst=%s new_weights=%s",
        shift_fraction,
        best,
        worst,
        out,
    )
    return out


def best_and_worst_avenues(pnls: Dict[str, float]) -> Tuple[Optional[str], Optional[str]]:
    if not pnls:
        return None, None
    best = max(pnls, key=lambda k: float(pnls[k]))
    worst = min(pnls, key=lambda k: float(pnls[k]))
    return best, worst
