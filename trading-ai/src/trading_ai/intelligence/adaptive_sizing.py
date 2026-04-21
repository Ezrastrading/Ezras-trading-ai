"""
Adaptive position sizing from recent realized outcomes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STRONG_WIN_RATE = 0.55
LOSS_STREAK_HALT = 5
NEGATIVE_AVG_LOSS_COUNT = 10
MAX_MULTIPLIER_CAP = 3.0


def _closed_trades_sorted(trades: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    closed = [
        t
        for t in trades
        if isinstance(t, dict) and str(t.get("outcome", "pending")).lower() not in ("pending", "")
    ]

    def _key(t: Dict[str, Any]) -> float:
        ts = str(t.get("resolved_at") or t.get("timestamp") or "")
        try:
            from datetime import datetime

            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0.0

    closed.sort(key=_key, reverse=True)
    return closed[: max(0, int(limit))]


def compute_size_multiplier(
    recent_trades: List[Dict[str, Any]],
    current_balance: float,
    *,
    max_multiplier: float = MAX_MULTIPLIER_CAP,
) -> float:
    """
    Derive a multiplier in (0, max_multiplier] from last ~20 closed trades.

    - 5 consecutive losses → *= 0.5
    - 10 losses in sample or negative average PnL → 0 (halt sizing)
    - strong performance (win_rate & avg pnl) → *= 1.1 (capped)
    """
    _ = max(0.0, float(current_balance))
    sample = _closed_trades_sorted(recent_trades, 20)
    if not sample:
        return 1.0

    wins = [t for t in sample if str(t.get("outcome")).lower() == "win"]
    losses = [t for t in sample if str(t.get("outcome")).lower() == "loss"]
    win_rate = len(wins) / len(sample) if sample else 0.0
    pnls = [float(t.get("pnl_usd", 0) or 0) for t in sample]
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0

    # loss streak (most recent first)
    streak = 0
    for t in sample:
        if str(t.get("outcome")).lower() == "loss":
            streak += 1
        else:
            break

    mult = 1.0

    if len(losses) >= NEGATIVE_AVG_LOSS_COUNT or avg_pnl < 0:
        logger.info(
            "adaptive_sizing: HALT multiplier=0.0 losses=%s avg_pnl=%.4f",
            len(losses),
            avg_pnl,
        )
        return 0.0

    if streak >= LOSS_STREAK_HALT:
        mult *= 0.5
        logger.info("adaptive_sizing: loss_streak=%s → multiplier scale 0.5", streak)

    if win_rate >= STRONG_WIN_RATE and avg_pnl > 0:
        mult *= 1.1
        logger.info(
            "adaptive_sizing: strong performance win_rate=%.3f avg_pnl=%.4f → boost 1.1x",
            win_rate,
            avg_pnl,
        )

    mult = max(0.0, min(float(max_multiplier), mult))
    try:
        from trading_ai.intelligence.deployment_decision import profit_reality_enforcement_enabled, scaling_permitted

        if profit_reality_enforcement_enabled():
            ok, _ = scaling_permitted()
            if not ok:
                mult = min(mult, 1.0)
    except Exception:
        pass
    logger.info(
        "adaptive_sizing: win_rate=%.4f avg_pnl=%.4f size_multiplier=%.4f",
        win_rate,
        avg_pnl,
        mult,
    )
    return mult


def load_multiplier_from_journal(
    *,
    current_balance: float,
    max_multiplier: float = MAX_MULTIPLIER_CAP,
) -> float:
    """Convenience: load recent closed trades from the universal journal."""
    try:
        from trading_ai.shark.trade_journal import get_all_trades

        return compute_size_multiplier(get_all_trades(), current_balance, max_multiplier=max_multiplier)
    except Exception as exc:
        logger.warning("adaptive_sizing: journal load failed (%s), multiplier=1.0", exc)
        return 1.0
