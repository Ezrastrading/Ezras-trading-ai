from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class GapThresholds:
    """
    Thresholds must be explicitly configured (no defaults).
    """

    min_edge_percent: float
    min_confidence_score: float
    min_liquidity_score: float
    max_fees_estimate: Optional[float]
    max_slippage_estimate: Optional[float]


def _read_float(name: str) -> Tuple[Optional[float], Optional[str]]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None, "missing"
    try:
        return float(raw), None
    except ValueError:
        return None, "not_float"


def load_gap_thresholds_strict() -> Tuple[Optional[GapThresholds], Dict[str, Any]]:
    """
    Strict loader:
    - required env: GAP_MIN_EDGE_PERCENT, GAP_MIN_CONFIDENCE_SCORE, GAP_MIN_LIQUIDITY_SCORE
    - optional env: GAP_MAX_FEES_ESTIMATE, GAP_MAX_SLIPPAGE_ESTIMATE
    """
    diag: Dict[str, Any] = {"ok": False, "missing": [], "invalid": []}

    min_edge, err = _read_float("GAP_MIN_EDGE_PERCENT")
    if err:
        (diag["missing"] if err == "missing" else diag["invalid"]).append("GAP_MIN_EDGE_PERCENT")
    min_conf, err = _read_float("GAP_MIN_CONFIDENCE_SCORE")
    if err:
        (diag["missing"] if err == "missing" else diag["invalid"]).append("GAP_MIN_CONFIDENCE_SCORE")
    min_liq, err = _read_float("GAP_MIN_LIQUIDITY_SCORE")
    if err:
        (diag["missing"] if err == "missing" else diag["invalid"]).append("GAP_MIN_LIQUIDITY_SCORE")

    max_fees_raw = (os.environ.get("GAP_MAX_FEES_ESTIMATE") or "").strip()
    max_slip_raw = (os.environ.get("GAP_MAX_SLIPPAGE_ESTIMATE") or "").strip()
    max_fees: Optional[float] = None
    max_slip: Optional[float] = None
    if max_fees_raw:
        try:
            max_fees = float(max_fees_raw)
        except ValueError:
            diag["invalid"].append("GAP_MAX_FEES_ESTIMATE")
    if max_slip_raw:
        try:
            max_slip = float(max_slip_raw)
        except ValueError:
            diag["invalid"].append("GAP_MAX_SLIPPAGE_ESTIMATE")

    if diag["missing"] or diag["invalid"] or min_edge is None or min_conf is None or min_liq is None:
        diag["ok"] = False
        return None, diag

    diag["ok"] = True
    return (
        GapThresholds(
            min_edge_percent=float(min_edge),
            min_confidence_score=float(min_conf),
            min_liquidity_score=float(min_liq),
            max_fees_estimate=max_fees,
            max_slippage_estimate=max_slip,
        ),
        diag,
    )

