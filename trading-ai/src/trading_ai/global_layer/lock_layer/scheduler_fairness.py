"""Fairness scheduler — rank bots for run order under budget pressure."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.global_layer.lock_layer.quality_contract import compute_bot_quality_contract
from trading_ai.global_layer.lock_layer.constitution import OBJECTIVE_HIERARCHY


def schedule_bots_fairness(bots: List[Dict[str, Any]], *, max_run: int = 32) -> List[Dict[str, Any]]:
    """
    Higher composite quality + lower token pressure runs first.
    Objective hierarchy: prefer bots that protect capital (proxy: truth_clean + low conflict).
    """
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for b in bots:
        qc = compute_bot_quality_contract(b)
        tok = float(b.get("token_budget_remaining") or 0.0)
        urgency = 1.0 if b.get("demotion_risk") else 0.0
        pri = float(qc["composite_quality"]) * 0.7 + min(1.0, tok / 40_000.0) * 0.2 + urgency * 0.1
        scored.append((pri, {**b, "quality_contract": qc, "schedule_priority": round(pri, 6)}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:max_run]]


def objective_rank_for_bot(bot: Dict[str, Any]) -> int:
    """Lower is higher priority lane (matches OBJECTIVE_HIERARCHY emphasis on safety)."""
    _ = bot  # reserved for lane-specific mapping
    return len(OBJECTIVE_HIERARCHY)
