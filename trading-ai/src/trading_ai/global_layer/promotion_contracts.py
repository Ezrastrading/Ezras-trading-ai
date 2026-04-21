"""Explicit promotion / demotion / disable contracts — no live authority without approved contract."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.orchestration_paths import (
    demotion_queue_path,
    disable_queue_path,
    promotion_queue_path,
)
from trading_ai.global_layer.orchestration_schema import PermissionLevel


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_queue(p: Path) -> Dict[str, Any]:
    if not p.is_file():
        return {"truth_version": "queue_v1", "items": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_queue(p: Path, data: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _iso()
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def propose_promotion(
    bot_id: str,
    *,
    target_level: str,
    evidence_refs: List[str],
    requested_by: str,
) -> Dict[str, Any]:
    if target_level in (PermissionLevel.EXECUTION_AUTHORITY.value, PermissionLevel.ADMIN_INTERNAL.value):
        raise ValueError("direct_execution_authority_requires_grant_path_not_queue")
    item = {
        "contract_id": f"promo_{uuid.uuid4().hex[:12]}",
        "bot_id": bot_id,
        "target_permission_level": target_level,
        "evidence_refs": evidence_refs,
        "requested_by": requested_by,
        "status": "pending",
        "created_at": _iso(),
    }
    q = _load_queue(promotion_queue_path())
    q.setdefault("items", []).append(item)
    _save_queue(promotion_queue_path(), q)
    return item


def evaluate_promotion_contract(bot: Dict[str, Any], contract: Dict[str, Any]) -> Tuple[bool, str]:
    """Deterministic checks — extend with replay artifacts when wired."""
    perf = bot.get("performance") or {}
    comp = perf.get("composite") or {}
    rel = float(comp.get("trust_score") or bot.get("reliability_score") or 0.0)
    if rel < 0.55:
        return False, "reliability_below_threshold"
    hb = str(bot.get("last_heartbeat_at") or "")
    if not hb:
        return False, "missing_heartbeat"
    if bot.get("demotion_risk") is True:
        return False, "demotion_risk_set"
    return True, "ok"


def approve_promotion_contract(contract_id: str, approver: str, bot: Dict[str, Any]) -> Dict[str, Any]:
    q = _load_queue(promotion_queue_path())
    items = []
    found = None
    for it in q.get("items") or []:
        it = dict(it)
        if str(it.get("contract_id")) == contract_id:
            ok, why = evaluate_promotion_contract(bot, it)
            if not ok:
                it["status"] = "rejected"
                it["reject_reason"] = why
            else:
                it["status"] = "approved"
                it["approved_by"] = approver
                it["approved_at"] = _iso()
            found = it
        items.append(it)
    q["items"] = items
    _save_queue(promotion_queue_path(), q)
    if not found:
        raise ValueError("contract_not_found")
    return found


def propose_demotion(bot_id: str, reason: str, requested_by: str) -> Dict[str, Any]:
    item = {
        "contract_id": f"demo_{uuid.uuid4().hex[:12]}",
        "bot_id": bot_id,
        "reason": reason,
        "requested_by": requested_by,
        "status": "pending",
        "created_at": _iso(),
    }
    q = _load_queue(demotion_queue_path())
    q.setdefault("items", []).append(item)
    _save_queue(demotion_queue_path(), q)
    return item


def propose_disable(bot_id: str, reason: str, requested_by: str) -> Dict[str, Any]:
    item = {
        "contract_id": f"dis_{uuid.uuid4().hex[:12]}",
        "bot_id": bot_id,
        "reason": reason,
        "requested_by": requested_by,
        "status": "pending",
        "created_at": _iso(),
    }
    q = _load_queue(disable_queue_path())
    q.setdefault("items", []).append(item)
    _save_queue(disable_queue_path(), q)
    return item

