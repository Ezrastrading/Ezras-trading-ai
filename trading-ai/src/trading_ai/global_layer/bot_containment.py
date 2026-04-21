"""Kill switch / containment — freezes creation, restricts to trusted bots, avenue/gate scoped."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.global_layer._bot_paths import global_layer_governance_dir


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def containment_path() -> Path:
    return global_layer_governance_dir() / "bot_containment.json"


def load_containment() -> Dict[str, Any]:
    p = containment_path()
    if not p.is_file():
        return {
            "truth_version": "bot_containment_v1",
            "freeze_all_new_bots": False,
            "disable_experimental": False,
            "trusted_bot_ids_only": False,
            "revert_to_baseline": False,
            "avenue_containment": {},
            "gate_containment": {},
            "updated_at": None,
        }
    return json.loads(p.read_text(encoding="utf-8"))


def save_containment(cfg: Dict[str, Any]) -> None:
    p = containment_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(cfg)
    cfg["updated_at"] = _iso()
    p.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def freeze_new_bot_creation(enabled: bool = True) -> Dict[str, Any]:
    c = load_containment()
    c["freeze_all_new_bots"] = bool(enabled)
    save_containment(c)
    return c


def set_avenue_containment(avenue: str, frozen: bool) -> Dict[str, Any]:
    c = load_containment()
    ac = dict(c.get("avenue_containment") or {})
    ac[str(avenue)] = bool(frozen)
    c["avenue_containment"] = ac
    save_containment(c)
    return c
