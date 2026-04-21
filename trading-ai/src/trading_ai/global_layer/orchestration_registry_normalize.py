"""Merge legacy / partial bot dicts into full orchestration v2 records."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.global_layer.orchestration_schema import (
    compute_assigned_scope,
    compute_canonical_owner_key,
    compute_duplicate_guard_key,
    default_bot_class_from_legacy_role,
    default_bot_record_skeleton,
)
from trading_ai.global_layer.system_mission import default_bot_mission_fields


def normalize_bot_record(cfg: Dict[str, Any]) -> Dict[str, Any]:
    bid = str(cfg.get("bot_id") or "").strip()
    avenue = str(cfg.get("avenue") or "").strip()
    gate = str(cfg.get("gate") or "none").strip()
    role = str(cfg.get("role") or "SCANNER").strip()
    version = str(cfg.get("version") or "v1").strip()
    base = default_bot_record_skeleton(bid, avenue, gate, role, version)
    merged = dict(base)
    for k, v in cfg.items():
        if v is not None:
            merged[k] = v
    route = str(merged.get("route") or "default")
    bc = str(merged.get("bot_class") or default_bot_class_from_legacy_role(role))
    scope = str(merged.get("assigned_scope") or compute_assigned_scope(avenue, gate, route))
    merged["route"] = route
    merged["bot_class"] = bc
    merged["assigned_scope"] = scope
    merged["duplicate_guard_key"] = compute_duplicate_guard_key(avenue, gate, route, bc, scope)
    merged["canonical_owner_key"] = compute_canonical_owner_key(avenue, gate, route, bid)
    for k, v in default_bot_mission_fields().items():
        merged.setdefault(k, v)
    _score_keys = (
        "profitability_score",
        "upside_speed_score",
        "convergence_score",
        "scale_score",
        "truth_score",
        "capital_efficiency_score",
        "token_efficiency_score",
        "implementation_speed_score",
        "promotion_velocity_score",
        "research_usefulness_score",
        "progression_score",
        "evidence_streak_score",
    )
    for sk in _score_keys:
        merged.setdefault(sk, 0.0)
    return merged
