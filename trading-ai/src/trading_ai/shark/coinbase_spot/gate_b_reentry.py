"""Re-entry cooldown + breakout confirmation gate."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class ReentryController:
    cooldown_sec: float = 300.0
    _last_exit: Dict[str, float] = field(default_factory=dict)
    _blocked: Dict[str, List[str]] = field(default_factory=dict)

    def record_exit(self, product_id: str, *, now: float | None = None) -> None:
        self._last_exit[product_id] = float(now or time.time())

    def block_negative_lesson(self, product_id: str, reason: str = "negative_lesson_block") -> None:
        self._blocked.setdefault(product_id, []).append(reason)

    def can_reenter(
        self,
        product_id: str,
        *,
        momentum_score: float,
        new_breakout_confirmed: bool,
    ) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if self._blocked.get(product_id):
            reasons.extend(self._blocked[product_id])
        ts = self._last_exit.get(product_id)
        if ts is not None and (time.time() - ts) < float(self.cooldown_sec):
            reasons.append("cooldown_active")
        if not new_breakout_confirmed:
            reasons.append("new_breakout_not_confirmed")
        if momentum_score < 0.2:
            reasons.append("momentum_not_reset")
        return (len(reasons) == 0, reasons)
