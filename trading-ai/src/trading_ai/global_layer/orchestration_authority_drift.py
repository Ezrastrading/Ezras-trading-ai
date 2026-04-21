"""Detect drift between execution_authority.json slots and bot registry permission claims."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.execution_authority import load_authority_registry
from trading_ai.global_layer.execution_authority import slot_key as authority_slot_key
from trading_ai.global_layer.orchestration_schema import PermissionLevel


def detect_authority_drift(*, registry_path=None) -> Dict[str, Any]:
    """
    Returns structured findings. Any ``mismatch`` or ``orphan_slot`` is a blocker for supervised-ready.
    """
    reg = load_authority_registry()
    bots = load_registry(registry_path).get("bots") or []
    slots = list(reg.get("slots") or [])
    findings: List[Dict[str, Any]] = []
    by_id = {str(b.get("bot_id")): b for b in bots}

    for s in slots:
        sk = str(s.get("slot_key") or "")
        bid = str(s.get("bot_id") or "")
        b = by_id.get(bid)
        if not b:
            findings.append({"kind": "orphan_slot", "slot_key": sk, "bot_id": bid, "detail": "bot_missing_from_registry"})
            continue
        pl = str(b.get("permission_level") or "")
        if pl != PermissionLevel.EXECUTION_AUTHORITY.value:
            findings.append(
                {
                    "kind": "permission_mismatch",
                    "slot_key": sk,
                    "bot_id": bid,
                    "detail": "registry_permission_not_execution_authority",
                    "permission_level": pl,
                }
            )
        av = str(s.get("avenue") or "")
        gt = str(s.get("gate") or "")
        rt = str(s.get("route") or "default")
        if authority_slot_key(av, gt, rt) != sk:
            findings.append({"kind": "slot_key_format_drift", "slot_key": sk, "detail": "recompute_mismatch"})

    holders = {str(s.get("bot_id")) for s in slots}
    for b in bots:
        pl = str(b.get("permission_level") or "")
        bid = str(b.get("bot_id") or "")
        if pl == PermissionLevel.EXECUTION_AUTHORITY.value and bid not in holders:
            findings.append(
                {
                    "kind": "registry_claim_without_slot",
                    "bot_id": bid,
                    "detail": "execution_authority_permission_without_execution_authority_json_slot",
                }
            )

    blocked = any(f.get("kind") in ("orphan_slot", "permission_mismatch", "registry_claim_without_slot") for f in findings)
    return {
        "truth_version": "authority_drift_v1",
        "blocked": blocked,
        "findings": findings,
        "slot_count": len(slots),
    }


def assert_no_authority_drift(*, registry_path=None) -> Tuple[bool, str]:
    d = detect_authority_drift(registry_path=registry_path)
    if d.get("blocked"):
        return False, "authority_drift_detected"
    return True, "ok"
