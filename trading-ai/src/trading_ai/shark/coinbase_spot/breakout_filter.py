"""Breakout / continuation filter for Gate B rows."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


def evaluate_breakout_entry(
    row: Mapping[str, Any],
    *,
    min_move_pct: float,
    min_volume_surge_ratio: float,
    min_continuation_candles: int,
    min_momentum_score: float,
) -> Dict[str, Any]:
    move = float(row.get("move_pct") or 0.0)
    vsr = float(row.get("volume_surge_ratio") or 0.0)
    cont = int(row.get("continuation_candles") or 0)
    vel = float(row.get("velocity_score") or 0.0)
    cs = float(row.get("candle_structure_score") or 0.0)
    mom = 0.55 * min(1.0, move / max(min_move_pct, 1e-9))
    mom += 0.25 * min(1.0, vsr / max(min_volume_surge_ratio, 1e-9))
    mom += 0.1 * min(1.0, cont / max(min_continuation_candles, 1))
    mom += 0.05 * vel + 0.05 * cs
    mom = min(1.0, mom)
    reasons: List[str] = []
    ok = move >= min_move_pct and vsr >= min_volume_surge_ratio and cont >= min_continuation_candles and mom >= min_momentum_score
    if not ok:
        reasons.append("momentum_below_threshold")
    return {"passed": ok, "momentum_score": mom, "reject_reasons": reasons}
