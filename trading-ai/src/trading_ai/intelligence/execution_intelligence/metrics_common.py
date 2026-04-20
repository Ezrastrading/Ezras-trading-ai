"""Shared PnL / strategy score helpers (no circular imports)."""

from __future__ import annotations

from typing import Any, Dict, List


def collect_strategy_scores(ss: Dict[str, Any]) -> List[float]:
    out: List[float] = []
    av = ss.get("avenues") or {}
    if not isinstance(av, dict):
        return out
    for _aid, block in av.items():
        if not isinstance(block, dict):
            continue
        for _sk, row in block.items():
            if not isinstance(row, dict):
                continue
            v = row.get("score")
            if v is None:
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
    return out


def max_drawdown_cumulative_pnls(pnls: List[float]) -> float:
    if not pnls:
        return 0.0
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return float(worst)
