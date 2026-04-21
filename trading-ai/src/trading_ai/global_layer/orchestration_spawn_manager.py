"""Spawn policy — global/avenue/gate/class caps, cooldown, dedupe, audit trail."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.bot_types import BotLifecycleState
from trading_ai.global_layer.orchestration_paths import last_spawn_ts_path, spawn_audit_path
from trading_ai.global_layer import orchestration_schema as _orch_schema

MAX_BOTS_GLOBAL = _orch_schema.MAX_BOTS_GLOBAL
MAX_BOTS_PER_AVENUE = _orch_schema.MAX_BOTS_PER_AVENUE
MAX_BOTS_PER_CLASS = _orch_schema.MAX_BOTS_PER_CLASS
MAX_BOTS_PER_GATE = _orch_schema.MAX_BOTS_PER_GATE

_ACTIVE_LIKE = frozenset(
    {
        BotLifecycleState.INITIALIZED.value,
        BotLifecycleState.SHADOW.value,
        BotLifecycleState.ELIGIBLE.value,
        BotLifecycleState.PROBATION.value,
        BotLifecycleState.ACTIVE.value,
        BotLifecycleState.PROMOTED.value,
        BotLifecycleState.DEGRADED.value,
    }
)


def _is_active_like(b: Dict[str, Any]) -> bool:
    return str(b.get("lifecycle_state") or "") in _ACTIVE_LIKE


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_spawn_audit(row: Dict[str, Any]) -> None:
    p = spawn_audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _load_last_spawn() -> float:
    p = last_spawn_ts_path()
    if not p.is_file():
        return 0.0
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return float(d.get("ts") or 0.0)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0.0


def _save_last_spawn() -> None:
    p = last_spawn_ts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ts": time.time(), "iso": _iso()}, indent=2) + "\n", encoding="utf-8")


def evaluate_spawn_policy(
    *,
    normalized_bot: Dict[str, Any],
    registry_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    active = [b for b in bots if _is_active_like(b)]
    if len(active) >= MAX_BOTS_GLOBAL:
        return False, "max_global_active_bots"
    avenue = str(normalized_bot.get("avenue") or "")
    gate = str(normalized_bot.get("gate") or "none")
    bclass = str(normalized_bot.get("bot_class") or "")
    av_active = [b for b in active if str(b.get("avenue")) == avenue]
    if len(av_active) >= MAX_BOTS_PER_AVENUE:
        return False, "max_active_bots_per_avenue"
    gate_count = sum(1 for b in active if str(b.get("avenue")) == avenue and str(b.get("gate")) == gate)
    if gate_count >= MAX_BOTS_PER_GATE:
        return False, "max_active_bots_per_gate"
    cls_active = [b for b in active if str(b.get("bot_class")) == bclass]
    if len(cls_active) >= MAX_BOTS_PER_CLASS:
        return False, "max_active_bots_per_class"
    last = _load_last_spawn()
    cooldown = int(_orch_schema.SPAWN_COOLDOWN_SEC)
    if last and (time.time() - last) < cooldown:
        return False, "spawn_cooldown"
    dg = str(normalized_bot.get("duplicate_guard_key") or "")
    for b in bots:
        if str(b.get("duplicate_guard_key")) == dg:
            return False, "duplicate_scope_in_registry"
    return True, "ok"


def audit_spawn_decision(allowed: bool, reason: str, context: Dict[str, Any]) -> None:
    _append_spawn_audit(
        {
            "allowed": allowed,
            "reason": reason,
            "context": context,
            "at": _iso(),
        }
    )
    if allowed:
        _save_last_spawn()
