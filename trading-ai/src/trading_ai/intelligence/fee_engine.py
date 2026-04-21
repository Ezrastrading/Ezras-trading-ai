"""
Fee-Aware Decision Layer — block trades where round-trip fees consume the edge.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def estimate_total_fees(trade_size_usd: float, fee_rate: float) -> float:
    """Round-trip fees: open + close (same rate each leg)."""
    return float(trade_size_usd) * float(fee_rate) * 2.0


def is_trade_profitable(expected_profit: float, fees: float) -> bool:
    return float(expected_profit) > float(fees)


def evaluate_fee_gate(
    *,
    trade_size_usd: float,
    fee_rate: float,
    expected_profit_usd: float,
) -> Dict[str, Any]:
    """
    Returns dict with fees, profitable flag, and whether execution should be blocked.
    Block when expected_profit <= fees.
    """
    fees = estimate_total_fees(trade_size_usd, fee_rate)
    profitable = is_trade_profitable(expected_profit_usd, fees)
    payload = {
        "expected_profit": float(expected_profit_usd),
        "fees": float(fees),
        "profitable": bool(profitable),
    }
    logger.info("fee_engine: %s", payload)
    return payload
