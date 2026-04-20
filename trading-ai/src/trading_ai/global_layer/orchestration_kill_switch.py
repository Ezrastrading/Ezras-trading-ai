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
            "venue_family": {},
            "gate": {},
            "bot_class": {},
            "bot_id": {},
            "degrade_to_observe_only": True,
            "updated_at": None,
        }
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "venue_family" not in raw:
        raw["venue_family"] = {}
    return raw


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


def freeze_venue_family(family_id: str, enabled: bool = True) -> Dict[str, Any]:
    """
    Freeze all avenues in :data:`VENUE_FAMILY_MEMBERS` for ``family_id`` and record the family flag.

    Does not set ``orchestration_frozen`` globally.
    """
    from trading_ai.global_layer.venue_family_contract import avenues_for_venue_family

    c = load_kill_switch()
    av = dict(c.get("avenue") or {})
    vf = dict(c.get("venue_family") or {})
    fid = str(family_id or "").strip()
    if not fid:
        return c
    aids = avenues_for_venue_family(fid)
    if enabled:
        vf[fid] = True
        for a in aids:
            av[str(a).strip()] = True
    else:
        vf.pop(fid, None)
        for a in aids:
            av.pop(str(a).strip(), None)
    c["venue_family"] = vf
    c["avenue"] = av
    save_kill_switch(c)
    return {
        **c,
        "freeze_venue_family_applied": fid,
        "freeze_target_avenue_ids": list(aids),
    }


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
    vf_map = c.get("venue_family") or {}
    if isinstance(vf_map, dict):
        from trading_ai.global_layer.venue_family_contract import venue_family_for_avenue

        fam = venue_family_for_avenue(aid)
        if fam != "unknown" and vf_map.get(fam):
            return True, "venue_family_frozen"
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
