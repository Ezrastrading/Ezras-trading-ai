from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvenueBCapitalBucket:
    """
    Isolated capital bucket placeholder for Avenue B.
    Must never read/modify Avenue A capital state.
    """

    max_notional_usd: float
    used_notional_usd: float = 0.0

    def remaining(self) -> float:
        return max(0.0, float(self.max_notional_usd) - float(self.used_notional_usd))

