"""
Single execution authority per (avenue, gate, route) for **orchestration delegation**.

**Does not** replace Gate A / Gate B live proof paths. Canonical venue orders remain in existing execution
code until an explicit go-live ties this registry to that path. This file enforces uniqueness of *declared*
bot authority only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.orchestration_paths import execution_authority_path
from trading_ai.global_layer.orchestration_schema import PermissionLevel


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_authority_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or execution_authority_path()
    if not p.is_file():
        return {"truth_version": "execution_authority_v1", "slots": [], "updated_at": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save_authority_registry(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or execution_authority_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["truth_version"] = "execution_authority_v1"
    data["updated_at"] = _iso()
    p.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def slot_key(avenue: str, gate: str, route: str) -> str:
    return f"{avenue}|{gate}|{route}"


def get_holder(avenue: str, gate: str, route: str, *, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    reg = load_authority_registry(path=path)
    sk = slot_key(avenue, gate, route)
    for s in reg.get("slots") or []:
        if str(s.get("slot_key")) == sk:
            return dict(s)
    return None


def grant_execution_authority(
    *,
    bot_id: str,
    avenue: str,
    gate: str,
    route: str,
    contract_ref: str,
    approved_by: str,
    permission_level: str = PermissionLevel.EXECUTION_AUTHORITY.value,
) -> Dict[str, Any]:
    """
    Requires external validation: caller must verify promotion contract + uniqueness policy.
    """
    if permission_level != PermissionLevel.EXECUTION_AUTHORITY.value:
        raise ValueError("grant_execution_authority_requires_execution_authority_level")
    reg = load_authority_registry()
    slots: List[Dict[str, Any]] = list(reg.get("slots") or [])
    sk = slot_key(avenue, gate, route)
    slots = [s for s in slots if str(s.get("slot_key")) != sk]
    slots.append(
        {
            "slot_key": sk,
            "avenue": avenue,
            "gate": gate,
            "route": route,
            "bot_id": bot_id,
            "contract_ref": contract_ref,
            "approved_by": approved_by,
            "granted_at": _iso(),
            "honesty": "Does not bypass Gate A/B; binds orchestration slot only.",
        }
    )
    reg["slots"] = slots
    save_authority_registry(reg)
    return {"ok": True, "slot_key": sk, "bot_id": bot_id}


def revoke_execution_authority(avenue: str, gate: str, route: str, *, reason: str) -> Dict[str, Any]:
    reg = load_authority_registry()
    sk = slot_key(avenue, gate, route)
    slots = [s for s in (reg.get("slots") or []) if str(s.get("slot_key")) != sk]
    reg["slots"] = slots
    reg["last_revoke"] = {"slot_key": sk, "reason": reason, "at": _iso()}
    save_authority_registry(reg)
    return {"ok": True, "revoked": sk}


def assert_single_authority_invariant(path: Optional[Path] = None) -> Tuple[bool, List[str]]:
    reg = load_authority_registry(path=path)
    seen = set()
    errs: List[str] = []
    for s in reg.get("slots") or []:
        sk = str(s.get("slot_key") or "")
        if sk in seen:
            errs.append(f"duplicate_slot:{sk}")
        seen.add(sk)
    return len(errs) == 0, errs

