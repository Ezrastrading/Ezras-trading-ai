"""Gate A universe builder + row evaluation (non-live)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence


@dataclass
class GateAEvaluation:
    product_id: str
    accepted: bool
    reject_reasons: List[str]


def evaluate_gate_a_row(row: Mapping[str, Any], *, max_spread_bps: float | None = None) -> GateAEvaluation:
    import os

    limit = float(max_spread_bps if max_spread_bps is not None else os.environ.get("GATE_A_MAX_SPREAD_BPS") or 25)
    reasons: List[str] = []
    spread = float(row.get("spread_bps") or 0.0)
    pid = str(row.get("product_id") or "")
    if spread > limit:
        reasons.append("spread_above_max")
    liq = float(row.get("liquidity_score") or 0.0)
    if liq < 0.15:
        reasons.append("liquidity_low")
    vol = float(row.get("quote_volume_24h_usd") or row.get("volume_24h_usd") or 0.0)
    if vol < 1_000_000:
        reasons.append("volume_low")
    return GateAEvaluation(product_id=pid, accepted=len(reasons) == 0, reject_reasons=reasons)


def rank_gate_a_candidates(rows: Sequence[Mapping[str, Any]]) -> List[GateAEvaluation]:
    scored: List[tuple[float, GateAEvaluation]] = []
    for row in rows:
        ev = evaluate_gate_a_row(row)
        if not ev.accepted:
            continue
        pri = 1.0 if str(row.get("product_id") or "").upper().startswith("BTC") else 0.0
        score = pri + float(row.get("liquidity_score") or 0.0)
        scored.append((score, ev))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored]


def build_gate_a_universe_artifact(
    *,
    all_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    chosen_product_id: Optional[str] = None,
    live_market_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    fallback_placeholder_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    source_mode: Optional[str] = None,
) -> Dict[str, Any]:
    rows = list(all_rows or [])
    if live_market_rows is not None or fallback_placeholder_rows is not None:
        live = list(live_market_rows or [])
        fb = list(fallback_placeholder_rows or [])
        mode = source_mode or "derived_internal_priority_fallback"
        rejected: List[Dict[str, Any]] = []
        accepted: List[Dict[str, Any]] = []
        for r in live + fb:
            ev = evaluate_gate_a_row(r)
            d = dict(r)
            if ev.accepted:
                accepted.append(d)
            else:
                d["reject_reasons"] = ev.reject_reasons
                rejected.append(d)
        fallback_in_use = bool(fb) or any(str(r.get("_gate_a_row_origin")) == "fallback" for r in live)
        truth_complete = bool(live) and not fallback_in_use and all(
            not r.get("_placeholder_volume_prior") for r in live
        )
        return {
            "source_mode": mode,
            "fallback_in_use": fallback_in_use,
            "production_truth_complete": truth_complete,
            "accepted_count": len(accepted),
            "rejected": rejected,
            "chosen_product_id": chosen_product_id,
        }

    rejected = []
    accepted = []
    for r in rows:
        ev = evaluate_gate_a_row(r)
        if ev.accepted:
            accepted.append(dict(r))
        else:
            rejected.append({**dict(r), "reject_reasons": ev.reject_reasons})
    return {
        "source_mode": source_mode or "all_rows",
        "fallback_in_use": False,
        "production_truth_complete": True,
        "accepted_count": len(accepted),
        "rejected": rejected,
        "chosen_product_id": chosen_product_id,
    }
