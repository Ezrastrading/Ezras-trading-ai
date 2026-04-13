"""Runtime state: Bayesian, loss clusters, opportunity-burst (not clock windows), mandates."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from trading_ai.shark.models import HuntType


@dataclass
class MandateState:
    compounding_paused: bool = False
    gaps_paused: bool = False
    execution_paused: bool = False  # operator pause OR auto >40% DD — scanning continues


@dataclass
class LossClusterTracker:
    """3 consecutive losses in (strategy, hunt, outlet, category) -> 50% sizing."""

    _streak: Dict[Tuple[str, str, str, str], int] = field(default_factory=dict)

    def record_outcome(
        self,
        *,
        strategy: str,
        hunt_type: HuntType,
        outlet: str,
        market_category: str,
        win: bool,
    ) -> None:
        key = (strategy, hunt_type.value, outlet, market_category)
        if win:
            self._streak[key] = 0
        else:
            self._streak[key] = self._streak.get(key, 0) + 1

    def cluster_multiplier(
        self,
        *,
        strategy: str,
        hunt_type: HuntType,
        outlet: str,
        market_category: str,
    ) -> float:
        key = (strategy, hunt_type.value, outlet, market_category)
        return 0.5 if self._streak.get(key, 0) >= 3 else 1.0


@dataclass
class BayesianWeights:
    """Half-weight updates; strategy weight 0.5 default; clamp 0.1–1.0 for scoring."""

    min_trades_for_realloc: int = 15
    strategy_weights: Dict[str, float] = field(default_factory=lambda: {"default": 0.5})
    hunt_weights: Dict[str, float] = field(
        default_factory=lambda: {h.value: 0.5 for h in HuntType},
    )
    outlet_weights: Dict[str, float] = field(default_factory=dict)
    hour_edge_quality: Dict[int, float] = field(default_factory=dict)
    trade_count: int = 0

    def update_from_trade(
        self,
        *,
        strategy: str,
        hunt_types: List[HuntType],
        outlet: str,
        win: bool,
        hour_utc: Optional[int] = None,
    ) -> None:
        self.trade_count += 1
        h = 0.5
        outcome = 1.0 if win else 0.0
        self.strategy_weights[strategy] = (1 - h) * self.strategy_weights.get(strategy, 0.5) + h * outcome
        for ht in hunt_types:
            self.hunt_weights[ht.value] = (1 - h) * self.hunt_weights.get(ht.value, 0.5) + h * outcome
        self.outlet_weights[outlet] = (1 - h) * self.outlet_weights.get(outlet, 0.5) + h * outcome
        if hour_utc is not None:
            prev = self.hour_edge_quality.get(hour_utc, 0.5)
            self.hour_edge_quality[hour_utc] = (1 - h) * prev + h * outcome

    def strategy_performance_weight(self, strategy: str) -> float:
        raw = self.strategy_weights.get(strategy, 0.5)
        return max(0.1, min(1.0, raw))


@dataclass
class OpportunityBurstTracker:
    """3+ opportunities in 15 minutes -> faster scan (90s). Not a time-of-day window."""

    burst_window_seconds: float = 15 * 60
    _ts: Deque[float] = field(default_factory=deque)

    def record_opportunity(self, now: float) -> None:
        self._ts.append(now)
        cutoff = now - self.burst_window_seconds
        while self._ts and self._ts[0] < cutoff:
            self._ts.popleft()

    def is_hot(self, now: float) -> bool:
        cutoff = now - self.burst_window_seconds
        while self._ts and self._ts[0] < cutoff:
            self._ts.popleft()
        return len(self._ts) >= 3


@dataclass
class IdleLog:
    entries: List[Dict[str, Any]] = field(default_factory=list)

    def log_idle(self, reason: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self.entries.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "detail": detail or {},
            }
        )


MANDATE = MandateState()
LOSS_TRACKER = LossClusterTracker()
BAYES = BayesianWeights()
HOT = OpportunityBurstTracker()
IDLE = IdleLog()

# Back-compat alias
HotWindowTracker = OpportunityBurstTracker
