"""
Hard liquidity filter — NO liquidity → NO trade. Produces liquidity_score 0–1.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional


def evaluate_liquidity_gate(
    row: Mapping[str, Any],
    *,
    min_volume_24h_usd: float = 2_000_000.0,
    max_spread_bps: float = 50.0,
    min_depth_usd: float = 25_000.0,
) -> Dict[str, Any]:
    """
    Required row keys (best-effort — missing numeric fields fail closed):

    - ``volume_24h_usd`` — 24h notional volume
    - ``spread_bps`` — bid/ask spread (must be present to pass; missing is **not** treated as a huge spread)
    - ``book_depth_usd`` — depth near mid supporting the intended size (caller supplies)
    """
    reasons: List[str] = []
    vol = float(row.get("volume_24h_usd") or 0.0)

    raw_sp = row.get("spread_bps")
    spread_measurement_status: str
    measured_spread_bps: Optional[float]
    spread_unavailable_reason: Optional[str]

    if raw_sp is None or (isinstance(raw_sp, float) and math.isnan(raw_sp)):
        spread_measurement_status = "unavailable"
        measured_spread_bps = None
        spread_unavailable_reason = "spread_bps_missing_or_null"
        sp_for_threshold = None
        s_ok = False
        reasons.append("spread_not_measured_fail_closed")
    else:
        try:
            measured_spread_bps = float(raw_sp)
        except (TypeError, ValueError):
            spread_measurement_status = "unavailable"
            measured_spread_bps = None
            spread_unavailable_reason = "spread_bps_unparseable"
            sp_for_threshold = None
            s_ok = False
            reasons.append("spread_not_measured_fail_closed")
        else:
            spread_measurement_status = "measured"
            spread_unavailable_reason = None
            sp_for_threshold = measured_spread_bps
            s_ok = measured_spread_bps <= max_spread_bps
            if not s_ok:
                reasons.append("spread_above_max")

    dep = float(row.get("book_depth_usd") or 0.0)

    v_ok = vol >= min_volume_24h_usd
    d_ok = dep >= min_depth_usd

    if not v_ok:
        reasons.append("volume_24h_below_min")
    if not d_ok:
        reasons.append("book_depth_insufficient")

    # Score components 0–1 (do not treat missing spread as ~9999 bps — spread score stays honest)
    v_score = min(1.0, vol / max(min_volume_24h_usd, 1.0)) if min_volume_24h_usd > 0 else 0.0
    v_score = min(1.0, v_score)
    if sp_for_threshold is None:
        s_score = 0.0
    else:
        s_score = max(0.0, 1.0 - float(sp_for_threshold) / max(max_spread_bps, 1.0))
    d_score = min(1.0, dep / max(min_depth_usd, 1.0)) if min_depth_usd > 0 else 0.0
    liquidity_score = max(0.0, min(1.0, 0.4 * v_score + 0.35 * s_score + 0.25 * d_score))

    passed = v_ok and s_ok and d_ok

    def _prov(key: str) -> str:
        meta = row.get("liquidity_field_provenance") if isinstance(row.get("liquidity_field_provenance"), dict) else None
        if meta and key in meta:
            return str(meta[key])
        if row.get(key) is None and key == "volume_24h_usd":
            if row.get("quote_volume_24h_usd") is not None:
                return "caller_supplied_hint"
        return "caller_supplied_hint" if row.get(key) is not None else "unknown"

    spread_source = "row_spread_bps" if raw_sp is not None else "none"

    return {
        "passed": passed,
        "liquidity_score": liquidity_score,
        "reject_reasons": reasons if not passed else [],
        "spread_measurement_status": spread_measurement_status,
        "spread_source": spread_source,
        "spread_unavailable_reason": spread_unavailable_reason,
        "measured_spread_bps": measured_spread_bps,
        "components": {
            "volume_score": v_score,
            "spread_score": s_score,
            "depth_score": d_score,
        },
        "field_provenance": {
            "volume_24h_usd": _prov("volume_24h_usd"),
            "spread_bps": _prov("spread_bps"),
            "book_depth_usd": _prov("book_depth_usd"),
        },
        "liquidity_truth_confidence": 0.85
        if passed and _prov("volume_24h_usd") != "unknown" and spread_measurement_status == "measured"
        else 0.35,
    }
