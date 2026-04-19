"""Position sizing from edge confidence, drawdown, regime, and operating mode."""

from __future__ import annotations

import math
from trading_ai.edge.models import EdgeStatus
from trading_ai.organism.operating_modes import OperatingMode
from trading_ai.organism.types import RegimeBucket


def suggested_position_fraction(
    *,
    edge_status: str,
    expectancy: float,
    stability_score: float,
    edge_drawdown_ratio: float,
    regime: str,
    mode: OperatingMode,
    base_cap: float = 0.05,
) -> float:
    """
    Low confidence → very small size; validated + stable → gradual increase.

    ``edge_drawdown_ratio`` in [0,1] — higher means deeper drawdown vs edge PnL (reduces size).
    """
    st = edge_status
    if st == EdgeStatus.REJECTED.value or st == EdgeStatus.CANDIDATE.value:
        return 0.0
    if st == EdgeStatus.TESTING.value:
        cap = base_cap * 0.15
    elif st == EdgeStatus.VALIDATED.value:
        cap = base_cap * 0.45
    else:
        cap = base_cap

    conf = max(0.0, min(1.0, 0.5 * math.tanh(expectancy * 50.0) + 0.5 * stability_score))
    dd_pen = max(0.2, 1.0 - min(0.85, edge_drawdown_ratio))
    reg = (regime or "").upper()
    if reg == RegimeBucket.LOW_LIQUIDITY.value:
        dd_pen *= 0.5
    elif reg == RegimeBucket.VOLATILE.value:
        dd_pen *= 0.75

    raw = cap * conf * dd_pen

    if mode == OperatingMode.PRESSURE:
        raw *= 0.35
    elif mode == OperatingMode.OPPORTUNITY:
        raw *= min(1.35, 1.0 + 0.15 * conf)

    return max(0.0, min(base_cap, raw))


def drawdown_ratio_from_metrics(net_pnl: float, max_dd: float) -> float:
    if net_pnl <= 0:
        return 1.0
    return min(1.0, max_dd / max(abs(net_pnl), 1e-9))
