"""Simple rolling trade stats."""

from __future__ import annotations

from typing import Iterable, List


def compute_rolling_stats(pnls: Iterable[float]) -> dict:
    xs: List[float] = [float(x) for x in pnls]
    n = len(xs)
    wins = sum(1 for x in xs if x > 0)
    return {"n": n, "win_rate": (wins / n) if n else 0.0}
