"""
Deterministic specialist bot seeds per avenue×gate — unique ``bot_class`` avoids duplicate_guard collisions.

Call explicitly (CLI / CEO / smoke). Does not grant live authority (default shadow band).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.bot_registry import get_bot, register_bot
from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.orchestration_schema import (
    BOT_CLASS_SCANNER,
    BOT_CLASS_TRUTH_AUD,
    DEFAULT_SPAWN_PERMISSION,
)


def _specs(avenue: str, gate: str) -> List[Tuple[str, str, str, str, str]]:
    """bot_id, role, bot_class, task_family, spawn_reason"""
    ag = f"{avenue}_{gate}".replace("|", "_")
    return [
        (f"ezras_spec_{ag}_scanner", BotRole.SCANNER.value, BOT_CLASS_SCANNER, "scan", "canonical_seed_scanner"),
        (f"ezras_spec_{ag}_entry", BotRole.DECISION.value, "entry_bot", "entry", "canonical_seed_entry_optimizer"),
        (f"ezras_spec_{ag}_exit", BotRole.DECISION.value, "exit_bot", "exit", "canonical_seed_exit_optimizer"),
        (f"ezras_spec_{ag}_latency", BotRole.DECISION.value, "slippage_bot", "latency", "canonical_seed_latency_slippage"),
        (f"ezras_spec_{ag}_strategy", BotRole.DECISION.value, "route_bot", "strategy", "canonical_seed_strategy_route"),
        (f"ezras_spec_{ag}_analysis", BotRole.LEARNING.value, "learning_bot", "analysis", "canonical_seed_trade_analysis"),
        (f"ezras_spec_{ag}_research", BotRole.LEARNING.value, "research_bot", "research", "canonical_seed_research"),
        (f"ezras_spec_{ag}_optimization", BotRole.LEARNING.value, "score_bot", "optimization", "canonical_seed_optimization"),
        (f"ezras_spec_{ag}_audit", BotRole.RISK.value, BOT_CLASS_TRUTH_AUD, "audit", "canonical_seed_truth_audit"),
    ]


def ensure_canonical_specialists(
    *,
    avenue: str = "A",
    gate: str = "gate_a",
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    created: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    for bid, role, bot_class, task_family, reason in _specs(avenue, gate):
        if get_bot(bid, path=registry_path):
            skipped.append(bid)
            continue
        cfg: Dict[str, Any] = {
            "bot_id": bid,
            "role": role,
            "avenue": avenue,
            "gate": gate,
            "version": "v1",
            "bot_class": bot_class,
            "task_family": task_family,
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "permission_level": DEFAULT_SPAWN_PERMISSION,
            "spawn_reason": reason,
            "spawn_source_bot_id": "system_canonical_seed",
            "current_objective": "maximize_measured_edge_velocity_within_scope",
        }
        try:
            register_bot(cfg, path=registry_path)
            created.append(bid)
        except ValueError as exc:
            errors.append(f"{bid}:{exc}")
    return {
        "ok": len(errors) == 0,
        "created_bot_ids": created,
        "skipped_existing": skipped,
        "errors": errors,
    }


def _specs_gate_b(avenue: str, gate: str) -> List[Tuple[str, str, str, str, str]]:
    ag = f"{avenue}_{gate}".replace("|", "_")
    return [
        (f"ezras_spec_{ag}_gainer_scan", BotRole.SCANNER.value, BOT_CLASS_SCANNER, "gainer_scan", "gate_b_gainer_scanner"),
        (f"ezras_spec_{ag}_momentum_val", BotRole.DECISION.value, "route_bot", "momentum_validate", "gate_b_momentum_validation"),
        (f"ezras_spec_{ag}_entry_timing", BotRole.DECISION.value, "entry_bot", "entry_timing", "gate_b_entry_timing"),
        (f"ezras_spec_{ag}_exit_trail", BotRole.DECISION.value, "exit_bot", "trailing_exit", "gate_b_exit_trailing"),
        (f"ezras_spec_{ag}_post_trade", BotRole.LEARNING.value, "learning_bot", "post_trade", "gate_b_post_trade_analysis"),
        (f"ezras_spec_{ag}_opt_research", BotRole.LEARNING.value, "research_bot", "optimization", "gate_b_optimization_research"),
        (f"ezras_spec_{ag}_audit", BotRole.RISK.value, BOT_CLASS_TRUTH_AUD, "audit", "gate_b_truth_audit"),
    ]


def ensure_gate_b_specialists(
    *,
    avenue: str = "A",
    gate: str = "gate_b",
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    created: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    for bid, role, bot_class, task_family, reason in _specs_gate_b(avenue, gate):
        if get_bot(bid, path=registry_path):
            skipped.append(bid)
            continue
        cfg: Dict[str, Any] = {
            "bot_id": bid,
            "role": role,
            "avenue": avenue,
            "gate": gate,
            "version": "v1",
            "bot_class": bot_class,
            "task_family": task_family,
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "permission_level": DEFAULT_SPAWN_PERMISSION,
            "spawn_reason": reason,
            "spawn_source_bot_id": "system_canonical_seed_gate_b",
            "current_objective": "maximize_measured_momentum_lane_edge_within_gate_b",
        }
        try:
            register_bot(cfg, path=registry_path)
            created.append(bid)
        except ValueError as exc:
            errors.append(f"{bid}:{exc}")
    return {"ok": len(errors) == 0, "created_bot_ids": created, "skipped_existing": skipped, "errors": errors}


def ensure_avenue_a_all_specialists(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    a = ensure_canonical_specialists(avenue="A", gate="gate_a", registry_path=registry_path)
    b = ensure_gate_b_specialists(avenue="A", gate="gate_b", registry_path=registry_path)
    return {"gate_a": a, "gate_b": b, "ok": bool(a.get("ok")) and bool(b.get("ok"))}
