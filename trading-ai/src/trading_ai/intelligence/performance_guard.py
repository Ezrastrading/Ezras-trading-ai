"""Scale down or halt after repeated losses in the recent window."""

from typing import Any, Dict, List, Optional, Tuple


def adjust_for_loss_streak(last_trades: List[Dict[str, Any]]) -> Tuple[float, Optional[str]]:
    losses = [t for t in last_trades if float(t.get("net_pnl", 0)) < 0]
    if len(losses) >= 10:
        return 0.0, "halt"
    if len(losses) >= 5:
        return 0.5, "reduce_size"
    return 1.0, None
