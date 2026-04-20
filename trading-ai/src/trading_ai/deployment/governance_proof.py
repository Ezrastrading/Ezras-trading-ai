"""
Governance proof: env flags, joint review snapshot, dry gate vs policy intent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.deployment.paths import governance_proof_path
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.global_layer.governance_order_gate import (
    _caution_block_entries,
    _decide,
    _enforcement_enabled,
    _joint_invalid_under_strict_enforcement,
    check_new_order_allowed_full,
    load_joint_review_snapshot,
)


def _joint_review_path() -> Path:
    from trading_ai.governance.storage_architecture import global_memory_dir

    return global_memory_dir() / "joint_review_latest.json"


def prove_governance_behavior(*, write_file: bool = True) -> Dict[str, Any]:
    """
    Compare dry :func:`_decide` with :func:`check_new_order_allowed_full`.

    Fails on: dry vs full mismatch, caution policy violation, or joint invalid under enforcement
    while the full gate still allows (silent disagreement).

    Writes ``data/deployment/governance_proof.json``.
    """
    snap = load_joint_review_snapshot()
    dry_allowed, dry_reason = _decide(snap)

    ok_full, reason_full, audit = check_new_order_allowed_full(
        venue="coinbase",
        operation="deployment_proof",
        route="governance_proof",
        intent_id="governance_proof_dry",
        log_decision=False,
    )

    enforcement = _enforcement_enabled()
    caution_block = _caution_block_entries()
    mode = str(snap.get("live_mode") or "unknown")
    joint_invalid = _joint_invalid_under_strict_enforcement(snap)

    notes: List[str] = []
    if dry_allowed != ok_full:
        notes.append(f"dry_vs_full_mismatch dry={dry_allowed},{dry_reason} full={ok_full},{reason_full}")

    caution_mismatch = bool(mode == "caution" and enforcement and caution_block and ok_full)
    if caution_mismatch:
        notes.append("caution_block_expected_but_gate_allowed")

    enforcement_mismatch = bool(enforcement and joint_invalid and ok_full)
    if enforcement_mismatch:
        notes.append("joint_invalid_under_enforcement_but_gate_allowed")

    if enforcement and snap.get("stale"):
        notes.append("joint_review_stale_under_enforcement")

    proof_ok = len(notes) == 0

    jp = _joint_review_path()
    out: Dict[str, Any] = {
        "generated_at": iso_now(),
        "joint_review_path": str(jp),
        "joint_review_file_exists": jp.is_file(),
        "enforcement_enabled": enforcement,
        "caution_block_entries": caution_block,
        "joint_snapshot_summary": {
            "present": snap.get("present"),
            "empty": snap.get("empty"),
            "live_mode": snap.get("live_mode"),
            "integrity": snap.get("integrity"),
            "stale": snap.get("stale"),
            "joint_invalid_under_strict": joint_invalid,
        },
        "dry_decision": {"allowed": dry_allowed, "reason": dry_reason},
        "full_check": {"allowed": ok_full, "reason": reason_full, "audit_keys": list(audit.keys())},
        "caution_mismatch": caution_mismatch,
        "enforcement_mismatch": enforcement_mismatch,
        "governance_proof_ok": proof_ok,
        "governance_system_consistent": proof_ok,
        "governance_trading_permitted": bool(ok_full),
        "governance_trading_block_reason": None if ok_full else reason_full,
        "governance_semantics": (
            "governance_proof_ok / governance_system_consistent: dry gate and full gate agree (no silent bypass). "
            "This is NOT the same as permission to trade: see governance_trading_permitted and "
            "governance_trading_block_reason. When enforcement is on, missing or invalid joint review must block "
            "orders even if proof is consistent."
        ),
        "notes": notes,
    }
    if write_file:
        governance_proof_path().parent.mkdir(parents=True, exist_ok=True)
        governance_proof_path().write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out
