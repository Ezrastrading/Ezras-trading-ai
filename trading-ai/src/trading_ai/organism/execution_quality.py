"""Slippage, latency, spread — single execution_quality_score in [0,1]."""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional


def execution_quality_score(trade: Mapping[str, Any]) -> float:
    """
    Degrades toward 0 when slippage, latency, or spread are hostile vs expectations.

    Uses existing databank fields when present.
    """
    slip_in = _f(trade.get("entry_slippage_bps"))
    slip_out = _f(trade.get("exit_slippage_bps"))
    spread = _f(trade.get("spread_bps_entry"))
    fill_s = _f(trade.get("fill_seconds"))
    exp_edge = _f(trade.get("expected_net_edge_bps"))

    slip = max(slip_in, slip_out, 0.0)
    slip_pen = math.exp(-slip / 80.0) if slip > 0 else 1.0
    spread_pen = math.exp(-max(0.0, spread - 5.0) / 40.0)
    latency_pen = math.exp(-max(0.0, fill_s - 2.0) / 8.0) if fill_s > 0 else 1.0
    edge_bonus = 1.0
    if exp_edge > 0 and slip > exp_edge * 0.5:
        edge_bonus = 0.35

    raw = slip_pen * spread_pen * latency_pen * edge_bonus
    return max(0.0, min(1.0, raw))


def _f(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def attach_execution_quality(trade: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(trade)
    out["execution_quality_score"] = execution_quality_score(trade)
    return out
