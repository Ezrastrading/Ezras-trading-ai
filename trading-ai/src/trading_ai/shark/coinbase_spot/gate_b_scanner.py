"""Gate B candidate ranking + momentum scan bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from trading_ai.shark.coinbase_spot.momentum_scoring_engine import (
    MomentumScanResult,
    run_momentum_scan,
    snapshot_from_row,
)


@dataclass
class GateBCandidate:
    product_id: str
    score: float
    momentum_score: float
    liquidity_score: float
    momentum_score_0_100: float = 0.0
    component_scores: Dict[str, float] = field(default_factory=dict)


def gate_b_momentum_scan(
    rows: Sequence[Mapping[str, Any]],
    *,
    base_threshold: float,
    top_k: int,
) -> MomentumScanResult:
    snaps = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        s = snapshot_from_row(dict(row))
        if s is not None:
            snaps.append(s)
    return run_momentum_scan(snaps, base_threshold=base_threshold, top_k=top_k)


def gate_b_candidates_from_scan(
    scan: MomentumScanResult,
    *,
    max_results: int,
    min_liquidity_0_1: float,
) -> Tuple[List[GateBCandidate], List[Dict[str, Any]]]:
    by_pid = {r.product_id: r for r in scan.ranked}
    acc: List[GateBCandidate] = []
    rej: List[Dict[str, Any]] = []
    for pid in scan.selected_product_ids:
        r = by_pid.get(pid)
        if r is None:
            continue
        liq01 = min(1.0, max(0.0, float(r.components.liquidity_score) / 100.0))
        if liq01 < float(min_liquidity_0_1):
            rej.append({"product_id": pid, "reason": "liquidity_below_min", "liquidity": liq01})
            continue
        mom01 = min(1.0, max(0.0, float(r.momentum_score) / 100.0))
        acc.append(
            GateBCandidate(
                product_id=pid,
                score=0.55 * mom01 + 0.45 * liq01,
                momentum_score=mom01,
                liquidity_score=liq01,
                momentum_score_0_100=float(r.momentum_score),
                component_scores=r.components.as_dict(),
            )
        )
        if len(acc) >= int(max_results):
            break
    for r in scan.ranked:
        if r.product_id in scan.selected_product_ids:
            continue
        rej.append({"product_id": r.product_id, "reason": "not_selected", "near_peak": r.near_peak})
    return acc, rej


def rank_gate_b_candidates(
    rows: Sequence[Mapping[str, Any]],
    min_liquidity_score: float = 0.35,
    min_momentum_score: float = 0.35,
    max_results: int = 10,
) -> Tuple[List[GateBCandidate], List[Dict[str, Any]]]:
    pool: List[GateBCandidate] = []
    rej: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "")
        mom = float(row.get("momentum_score") or 0.0)
        liq = float(row.get("liquidity_score") or 0.0)
        if liq < float(min_liquidity_score) or mom < float(min_momentum_score):
            rej.append({"product_id": pid, "reason": "below_rank_mins"})
            continue
        exh = float(row.get("exhaustion_risk") or 0.0)
        score = 0.55 * mom + 0.4 * liq - 0.05 * exh
        pool.append(GateBCandidate(product_id=pid, score=score, momentum_score=mom, liquidity_score=liq))
    pool.sort(key=lambda c: c.score, reverse=True)
    acc = pool[: int(max_results)]
    for c in pool[int(max_results) :]:
        rej.append({"product_id": c.product_id, "reason": "rank_overflow"})
    return acc, rej
