"""Validate bot configs against constitution — no automatic live execution authority."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.orchestration_schema import PermissionLevel


def validate_bot_config(cfg: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    if not str(cfg.get("bot_id") or "").strip():
        errs.append("missing_bot_id")
    role = str(cfg.get("role") or "")
    if role not in {r.value for r in BotRole}:
        errs.append("invalid_role")
    pl = str(cfg.get("permission_level") or "").strip()
    if pl and pl not in {p.value for p in PermissionLevel}:
        errs.append("invalid_permission_level")
    if not str(cfg.get("avenue") or "").strip():
        errs.append("missing_avenue")
    life = str(cfg.get("lifecycle_state") or "")
    if life and life not in {s.value for s in BotLifecycleState}:
        errs.append("invalid_lifecycle_state")
    ver = str(cfg.get("version") or "").strip()
    if not ver:
        errs.append("missing_version")
    gate = str(cfg.get("gate") or "none")
    if not gate:
        errs.append("missing_gate")
    return len(errs) == 0, errs


def assert_not_live_execution_override(cfg: Dict[str, Any]) -> None:
    """Hard deny: bots must not embed live execution bypass flags."""
    if cfg.get("bypass_gate_a") or cfg.get("bypass_gate_b") or cfg.get("bypass_risk"):
        raise ValueError("bot_config_forbids_execution_bypass_flags")
