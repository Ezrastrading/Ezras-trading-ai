"""Policies for partial entry/exit fills (min useful size, cancel remainder, chase exit)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class PartialFillPolicy:
    """Minimum base size (asset units) to keep a partial entry; below → cancel rest."""

    min_base_btc: float = 1e-5
    min_base_eth: float = 1e-4


def min_useful_base(product_id: str, policy: Optional[PartialFillPolicy] = None) -> float:
    p = policy or PartialFillPolicy()
    u = product_id.upper()
    if "BTC" in u:
        return p.min_base_btc
    if "ETH" in u:
        return p.min_base_eth
    return 1e-6


def entry_partial_decision(
    product_id: str,
    filled_base: float,
    intended_base: float,
    *,
    policy: Optional[PartialFillPolicy] = None,
) -> Tuple[str, float]:
    """
    Returns (action, recorded_base) where action is ``keep`` | ``cancel_remainder``.

    If filled is below minimum useful size, cancel remainder and record actual filled.
    """
    m = min_useful_base(product_id, policy)
    if filled_base >= intended_base * 0.999:
        return "keep", filled_base
    if filled_base < m:
        return "cancel_remainder", filled_base
    return "keep", filled_base


def exit_should_continue(filled_base: float, remaining_base: float) -> bool:
    """Exit path: keep closing until flat."""
    return remaining_base > 0
