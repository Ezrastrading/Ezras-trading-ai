"""Recovery hints for operators — attaches to failure modes without mutating venue state."""

from __future__ import annotations

from typing import Any, Dict, List


def recovery_hints_for_reason(reason: str) -> Dict[str, Any]:
    """Deterministic mapping from orchestration ``reason`` strings to next steps."""
    r = str(reason or "")
    cmds: List[str] = []
    notes: List[str] = []
    if "duplicate_intent" in r or r == "duplicate_intent":
        cmds.append("Inspect execution_intent_ledger.jsonl for the intent_id; safe to skip re-submit.")
        notes.append("Replay produced the same intent_id — venue should not receive a second order.")
    elif "authority_drift" in r or "registry_claim_without_slot" in r:
        cmds.append("python -m trading_ai.deployment refresh-orchestration-truth-chain")
        cmds.append("Reconcile execution_authority.json with promotion contract + registry.")
    elif "daily_loss_cap" in r or r == "daily_loss_cap_breached":
        cmds.append("Halt trading; verify orchestration_risk_caps.json current_daily_realized_loss_usd.")
        cmds.append("python -m trading_ai.deployment orchestration-freeze --global (if CLI available)")
    elif "data_stale" in r:
        cmds.append("Refresh market data / runtime artifacts; check EZRAS_RUNTIME_ROOT freshness.")
    elif "permission_level_denies" in r or "not_canonical_holder" in r:
        cmds.append("Verify only one execution_authority slot per avenue|gate|route.")
        cmds.append("Shadow bots must not hold execution authority — check registry permission_level.")
    elif "global_orchestration_frozen" in r or r == "global_orchestration_frozen":
        cmds.append("Review orchestration_kill_switch.json; unfreeze only after incident triage.")
    elif "stale" in r.lower():
        cmds.append("python -m trading_ai.deployment orchestration-stale-sweep")
        cmds.append("python -m trading_ai.deployment orchestration-heartbeat --bot-id <id>")
    else:
        cmds.append("python -m trading_ai.deployment orchestration-status")
        cmds.append("python -m trading_ai.deployment refresh-orchestration-truth-chain")
    return {
        "truth_version": "orchestration_recovery_hints_v1",
        "reason": r,
        "safe_to_retry_immediately": r in ("duplicate_intent",),
        "suggested_commands": cmds,
        "notes": notes,
    }


def attach_recovery_to_truth(truth: Dict[str, Any]) -> Dict[str, Any]:
    """Merge recovery block when ``reason`` present."""
    out = dict(truth)
    reason = str(out.get("reason") or out.get("blocker_reason") or "")
    if reason:
        out["recovery"] = recovery_hints_for_reason(reason)
    return out
