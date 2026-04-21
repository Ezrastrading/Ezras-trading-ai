"""
Abstain / no-trade intelligence — default to skipping unless confidence and market are strong.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.7


def should_abstain(confidence: float, edge: float, market_ok: bool) -> bool:
    if float(confidence) < MIN_CONFIDENCE - 1e-12:
        logger.info(
            "abstain_engine: abstain reason=low_confidence confidence=%.4f min=%.2f",
            float(confidence),
            MIN_CONFIDENCE,
        )
        return True
    if not market_ok:
        logger.info("abstain_engine: abstain reason=market_not_ok")
        return True
    if float(edge) <= 0.0:
        logger.info("abstain_engine: abstain reason=non_positive_edge edge=%.6f", float(edge))
        return True
    return False
