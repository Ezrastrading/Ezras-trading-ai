"""Append-only equity curve for deployment drawdown / trend checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class EquityTracker:
    curve: List[float] = field(default_factory=list)

    def update(self, balance: float) -> None:
        self.curve.append(float(balance))

    def trend_up(self) -> bool:
        if len(self.curve) < 10:
            return False
        return float(self.curve[-1]) > float(self.curve[0])

    def to_dict(self) -> Dict[str, Any]:
        return {"curve": list(self.curve)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EquityTracker":
        raw = d.get("curve") if isinstance(d, dict) else None
        if not isinstance(raw, list):
            return cls()
        return cls(curve=[float(x) for x in raw])
