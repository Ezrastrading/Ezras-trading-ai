"""Unified orchestration kill switch — layers global / avenue / gate / class / bot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from trading_ai.global_layer.bot_registry import patch_bot
from trading_ai.global_layer.orchestration_paths import orchestration_kill_switch_path
from trading_ai.global_layer.orchestration_schema import OrchestrationBotStatus, PermissionLevel


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_kill_switch(path: Path | None = None) -> Dict[str, Any]:
    p = path or orchestration_kill_switch_path()
    if not p.is_file():
        return {
            "truth_version": "orchestration_kill_switch_v1",
            "orchestration_frozen": False,
            "avenue": {},
            "gate": {},
            "bot_class": {},
            "bot_id": {},
            "degrade_to_observe_only": True,
            "updated_at": None,
        }
    return json.loads(p.read_text(encoding="utf-8"))


def save_kill_switch(cfg: Dict[str, Any]) -> None:
    p = orchestration_kill_switch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(cfg)
    cfg["updated_at"] = _iso()
    p.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def freeze_orchestration(enabled: bool = True) -> Dict[str, Any]:
    c = load_kill_switch()
    c["orchestration_frozen"] = bool(enabled)
    save_kill_switch(c)
    return c


def freeze_avenue(avenue_id: str, enabled: bool = True) -> Dict[str, Any]:
    """Freeze orchestration for a single avenue id (does not set global ``orchestration_frozen``)."""
    c = load_kill_switch()
    av = dict(c.get("avenue") or {})
    aid = str(avenue_id or "").strip()
    if not aid:
        return c
    if enabled:
        av[aid] = True
    else:
        av.pop(aid, None)
    c["avenue"] = av
    save_kill_switch(c)
    return c


def orchestration_blocked_for_bot(bot: Dict[str, Any]) -> Tuple[bool, str]:
    c = load_kill_switch()
    if c.get("orchestration_frozen"):
        return True, "global_orchestration_frozen"
    aid = str(bot.get("avenue") or "")
    gate = str(bot.get("gate") or "")
    bclass = str(bot.get("bot_class") or "")
    bid = str(bot.get("bot_id") or "")
    if c.get("avenue", {}).get(aid):
        return True, "avenue_frozen"
    if c.get("gate", {}).get(f"{aid}|{gate}"):
        return True, "gate_frozen"
    if c.get("bot_class", {}).get(bclass):
        return True, "bot_class_frozen"
    if c.get("bot_id", {}).get(bid):
        return True, "bot_frozen"
    return False, "ok"


def degrade_bot_to_observe_only(bot_id: str, *, reason: str, registry_path=None) -> Dict[str, Any]:
    return patch_bot(
        bot_id,
        {
            "permission_level": PermissionLevel.OBSERVE_ONLY.value,
            "status": OrchestrationBotStatus.DEGRADED.value,
            "disable_reason": reason,
        },
        path=registry_path,
    )
