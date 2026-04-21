"""Rolling edge stats for Gate B pause / size hints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Deque, Dict, List
from collections import deque


@dataclass
class GateBEdgeStats:
    _pnl: Deque[float] = field(default_factory=lambda: deque(maxlen=40))

    def record_trade_net_pnl(self, net_pnl_usd: float) -> None:
        self._pnl.append(float(net_pnl_usd))

    def report(self) -> Dict[str, object]:
        if not self._pnl:
            return {"recommend_reduce_size": False, "recommend_pause_gate_b": False, "n": 0}
        neg = sum(1 for x in self._pnl if x < 0)
        n = len(self._pnl)
        pause = n >= 8 and neg >= int(0.75 * n)
        reduce = n >= 5 and neg >= int(0.55 * n)
        return {"recommend_reduce_size": reduce, "recommend_pause_gate_b": pause, "n": n}
