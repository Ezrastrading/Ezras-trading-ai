"""Global capital allocation hints — rebalance weights from quality (advisory only)."""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.lock_layer.quality_contract import compute_bot_quality_contract


def suggest_lane_weights(*, registry_path=None) -> Dict[str, Any]:
    reg = load_registry(registry_path)
    by_avenue: Dict[str, List[float]] = {}
    for b in reg.get("bots") or []:
        a = str(b.get("avenue") or "unknown")
        q = compute_bot_quality_contract(dict(b))["composite_quality"]
        by_avenue.setdefault(a, []).append(q)
    weights: Dict[str, float] = {}
    for a, qs in by_avenue.items():
        weights[a] = round(sum(qs) / max(len(qs), 1), 6)
    s = sum(weights.values()) or 1.0
    norm = {k: round(v / s, 6) for k, v in weights.items()}
    return {"truth_version": "capital_allocation_hint_v1", "normalized_weights_by_avenue": norm, "honesty": "Advisory; real allocation must flow through capital governor + CEO policy."}
