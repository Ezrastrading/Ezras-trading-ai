"""
Micro-live → scaled capital — **advisory sizing hints only**.

Never relaxes risk caps, kill switch, or venue gates. Callers must clamp to policy maxima.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CapitalScaleHint:
    suggested_quote_usd: float
    win_streak: int
    drawdown_streak: int
    confidence: float
    notes: str


def suggest_micro_quote_usd(
    *,
    base_quote_usd: float = 7.5,
    min_quote_usd: float = 5.0,
    max_quote_usd: float = 10.0,
    profitable_trades_since_reset: int = 0,
    losing_trades_since_reset: int = 0,
    rolling_winrate: Optional[float] = None,
) -> CapitalScaleHint:
    """
    Gradual ramp starting in micro band ($5–$10 default envelope).

    - Wins nudge size up slowly inside ``max_quote_usd``.
    - Losses nudge down toward ``min_quote_usd``.
    - Low winrate caps aggressiveness even with a streak.
    """
    b = max(0.01, float(base_quote_usd))
    lo = max(0.01, float(min_quote_usd))
    hi = max(lo, float(max_quote_usd))
    wins = max(0, int(profitable_trades_since_reset))
    losses = max(0, int(losing_trades_since_reset))
    wr = rolling_winrate
    conf = 0.5 if wr is None else max(0.0, min(1.0, (float(wr) - 0.45) / 0.25))
    step = 0.35
    q = b + min(hi - b, wins * step * conf) - min(b - lo, losses * step * (1.0 - conf * 0.5))
    q = max(lo, min(hi, round(q, 4)))
    return CapitalScaleHint(
        suggested_quote_usd=q,
        win_streak=wins,
        drawdown_streak=losses,
        confidence=round(conf, 4),
        notes="Advisory only; enforce venue + policy max notional separately.",
    )


def capital_scaler_snapshot_dict(hint: CapitalScaleHint) -> Dict[str, Any]:
    return {
        "truth_version": "capital_scaler_hint_v1",
        "suggested_quote_usd": hint.suggested_quote_usd,
        "win_streak": hint.win_streak,
        "drawdown_streak": hint.drawdown_streak,
        "confidence": hint.confidence,
        "notes": hint.notes,
    }
