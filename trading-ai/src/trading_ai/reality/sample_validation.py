"""Sample validation — Sharpe-like ratio, confidence bands, UNPROVEN vs VALIDATED_EDGE."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Dict, Sequence


@dataclass
class SampleValidationResult:
    trade_count: int
    cumulative_pnl: float
    pnl_std_dev: float
    sharpe_like: float
    confidence_level: str
    mark: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_count": self.trade_count,
            "cumulative_pnl": self.cumulative_pnl,
            "pnl_std_dev": self.pnl_std_dev,
            "sharpe_like": self.sharpe_like,
            "confidence_level": self.confidence_level,
            "mark": self.mark,
        }


def _confidence_band(n: int) -> str:
    if n < 20:
        return "LOW"
    if n < 50:
        return "MEDIUM"
    if n < 100:
        return "HIGH"
    return "STRONG"


def validate_sample(net_pnls: Sequence[float]) -> SampleValidationResult:
    series = [float(x) for x in net_pnls]
    n = len(series)
    cum = sum(series)
    if n == 0:
        return SampleValidationResult(0, 0.0, 0.0, 0.0, "LOW", "UNPROVEN")
    std = statistics.pstdev(series) if n > 1 else 0.0
    avg = cum / n
    sharpe = avg / std if std > 1e-12 else (0.0 if abs(avg) < 1e-12 else float("inf"))
    if sharpe == float("inf"):
        sharpe = 0.0
    conf = _confidence_band(n)
    if cum > 0 and conf in ("HIGH", "STRONG"):
        mark = "VALIDATED_EDGE"
    elif cum > 0:
        mark = "UNPROVEN"
    else:
        mark = "UNPROVEN"
    return SampleValidationResult(
        trade_count=n,
        cumulative_pnl=cum,
        pnl_std_dev=std,
        sharpe_like=float(sharpe),
        confidence_level=conf,
        mark=mark,
    )
