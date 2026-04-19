"""Explicit gates before promoting strategy toward live."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PromotionGates:
    min_sample_trades: int = 30
    min_net_expectancy_bps: float = 0.0
    max_drawdown_frac: float = 0.25
    min_regime_buckets: int = 2
    min_edge_after_fees_bps: float = 0.0
    require_ceo_ok: bool = False


def validate_promotion_gates(
    metrics: Dict[str, Any],
    *,
    gates: Optional[PromotionGates] = None,
) -> Tuple[bool, List[str]]:
    """
    ``metrics`` may include: n_trades, net_expectancy_bps, max_dd_frac,
    regime_buckets, edge_after_fees_bps, ceo_approved, contamination_flags.
    """
    g = gates or PromotionGates()
    errs: List[str] = []
    n = int(metrics.get("n_trades") or 0)
    if n < g.min_sample_trades:
        errs.append("insufficient_sample")
    ne = float(metrics.get("net_expectancy_bps") or 0.0)
    if ne < g.min_net_expectancy_bps:
        errs.append("net_expectancy")
    dd = float(metrics.get("max_dd_frac") or 0.0)
    if dd > g.max_drawdown_frac:
        errs.append("drawdown")
    rb = int(metrics.get("regime_buckets") or 0)
    if rb < g.min_regime_buckets:
        errs.append("regime_robustness")
    edge = float(metrics.get("edge_after_fees_bps") or 0.0)
    if edge < g.min_edge_after_fees_bps:
        errs.append("fees_viability")
    if metrics.get("contamination_from_other_avenue"):
        errs.append("contamination")
    if g.require_ceo_ok and not metrics.get("ceo_approved"):
        errs.append("ceo_approval")
    return len(errs) == 0, errs
