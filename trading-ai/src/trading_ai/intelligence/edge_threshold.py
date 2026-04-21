"""
Minimum edge threshold — avoid micro-edges that do not clear costs + buffer.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MIN_EDGE_USD = 0.30


def passes_edge_threshold(expected_profit: float, fees: float) -> Tuple[bool, Optional[str]]:
    """
    Require profit strictly above fees + MIN_EDGE_USD.

    Returns (ok, reason_or_none).
    """
    ep = float(expected_profit)
    f = float(fees)
    if ep <= f + MIN_EDGE_USD + 1e-12:
        logger.info(
            "edge_threshold: SKIP expected_profit=%.4f fees=%.4f min_buffer=%.2f reason=edge_below_threshold",
            ep,
            f,
            MIN_EDGE_USD,
        )
        return False, "edge_below_threshold"
    return True, None
