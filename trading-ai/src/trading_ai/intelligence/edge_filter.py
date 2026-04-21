"""Pre-trade edge vs fees — prefer NO_TRADE over BAD_TRADE."""

from typing import Optional, Tuple


def passes_edge_filter(expected_profit_usd: float, estimated_fees_usd: float) -> Tuple[bool, Optional[str]]:
    MIN_EDGE_USD = 0.30
    if expected_profit_usd <= estimated_fees_usd + MIN_EDGE_USD:
        return False, "edge_too_small"
    return True, None
