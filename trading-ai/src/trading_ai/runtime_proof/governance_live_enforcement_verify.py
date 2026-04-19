"""
Write governance verification artifacts under a runtime root.

- ``governance_proof/governance_live_enforcement_verified.json`` — legacy harness cases.
- ``governance_proof/governance_strict_verified.json`` — strict fail-closed proof (Blocker 1).

Runs isolated checks (subprocess-free) with temporary EZRAS_RUNTIME_ROOT under the given root.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full


def _write_joint(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def verify_and_write_artifact(runtime_root: Path) -> Dict[str, Any]:
    """
    Exercises enforcement + missing-joint fail-closed with a temp tree under runtime_root.

    Does not print secrets.
    """
    runtime_root = runtime_root.resolve()
    proof = runtime_root / "governance_proof"
    proof.mkdir(parents=True, exist_ok=True)
    work = proof / "_verify_work"
    work.mkdir(parents=True, exist_ok=True)

    saved_ezr = os.environ.get("EZRAS_RUNTIME_ROOT")
    cases: List[Dict[str, Any]] = []
    overall = "pass"

    try:
        os.environ["EZRAS_RUNTIME_ROOT"] = str(work)
        gdir = work / "shark" / "memory" / "global"
        joint = gdir / "joint_review_latest.json"

        # Case A: enforcement on, missing joint file → must deny
        os.environ["GOVERNANCE_ORDER_ENFORCEMENT"] = "true"
        os.environ["GOVERNANCE_MISSING_JOINT_BLOCKS"] = "true"
        os.environ["GOVERNANCE_STALE_JOINT_BLOCKS"] = "true"
        os.environ["GOVERNANCE_UNKNOWN_MODE_BLOCKS"] = "true"
        os.environ["GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS"] = "true"
        os.environ["GOVERNANCE_CAUTION_BLOCK_ENTRIES"] = "true"
        if joint.is_file():
            joint.unlink()
        ok_a, reason_a, _a = check_new_order_allowed_full(
            venue="coinbase",
            operation="verify_missing_joint",
            intent_id="case_a",
            log_decision=False,
        )
        pass_a = ok_a is False and "missing_joint" in reason_a
        cases.append({"case": "missing_joint_file_fail_closed", "allowed": ok_a, "reason": reason_a, "pass": pass_a})
        if not pass_a:
            overall = "fail"

        # Case B: healthy joint → allow (normal mode)
        _write_joint(
            joint,
            {
                "schema_version": "1.0",
                "joint_review_id": "jr_verify_ok",
                "packet_id": "pkt_v",
                "empty": False,
                "live_mode_recommendation": "normal",
                "review_integrity_state": "full",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        ok_b, reason_b, _b = check_new_order_allowed_full(
            venue="coinbase",
            operation="verify_healthy_joint",
            intent_id="case_b",
            log_decision=False,
        )
        pass_b = ok_b is True
        cases.append({"case": "healthy_joint_allowed", "allowed": ok_b, "reason": reason_b, "pass": pass_b})
        if not pass_b:
            overall = "fail"

        # Case C: enforcement off → advisory (allows even if we delete joint - reload empty)
        joint.unlink(missing_ok=True)
        os.environ["GOVERNANCE_ORDER_ENFORCEMENT"] = "false"
        os.environ["GOVERNANCE_MISSING_JOINT_BLOCKS"] = "true"
        ok_c, reason_c, _c = check_new_order_allowed_full(
            venue="coinbase",
            operation="verify_advisory",
            intent_id="case_c",
            log_decision=False,
        )
        pass_c = ok_c is True and "advisory" in reason_c
        cases.append({"case": "enforcement_off_advisory", "allowed": ok_c, "reason": reason_c, "pass": pass_c})
        if not pass_c:
            overall = "fail"

    finally:
        if saved_ezr is not None:
            os.environ["EZRAS_RUNTIME_ROOT"] = saved_ezr
        else:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)

    out: Dict[str, Any] = {
        "schema": "governance_live_enforcement_verified_v1",
        "enforcement_enabled": True,
        "fail_closed_conditions_active": True,
        "test_results": overall,
        "cases": cases,
        "note": "Uses isolated EZRAS_RUNTIME_ROOT under governance_proof/_verify_work; does not mutate operator joint file.",
    }
    (proof / "governance_live_enforcement_verified.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    strict = _verify_strict_fail_closed_and_write(runtime_root, proof, work)
    out["governance_strict_verified"] = strict["path"]
    return out


def _verify_strict_fail_closed_and_write(
    runtime_root: Path,
    proof_dir: Path,
    work: Path,
) -> Dict[str, Any]:
    """
    Isolated strict cases: missing, stale, degraded integrity, unknown live mode — all must deny when enforcement on.
    Writes ``governance_strict_verified.json``.
    """
    saved_ezr = os.environ.get("EZRAS_RUNTIME_ROOT")
    joint = work / "shark" / "memory" / "global" / "joint_review_latest.json"
    joint.parent.mkdir(parents=True, exist_ok=True)

    missing_ok = stale_ok = degraded_ok = unknown_ok = False
    try:
        os.environ["EZRAS_RUNTIME_ROOT"] = str(work)
        os.environ["GOVERNANCE_ORDER_ENFORCEMENT"] = "true"
        # Prove env toggles do not open the gate:
        os.environ.pop("GOVERNANCE_MISSING_JOINT_BLOCKS", None)
        os.environ.pop("GOVERNANCE_STALE_JOINT_BLOCKS", None)
        os.environ.pop("GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS", None)
        os.environ.pop("GOVERNANCE_UNKNOWN_MODE_BLOCKS", None)

        if joint.is_file():
            joint.unlink()
        ok, reason, _ = check_new_order_allowed_full(
            venue="coinbase", operation="strict_missing", log_decision=False
        )
        missing_ok = ok is False and "missing_joint" in reason

        _write_joint(
            joint,
            {
                "joint_review_id": "jr_stale",
                "packet_id": "pkt_s",
                "empty": False,
                "live_mode_recommendation": "normal",
                "review_integrity_state": "full",
                "generated_at": "2000-01-01T00:00:00Z",
            },
        )
        os.environ["GOVERNANCE_JOINT_STALE_HOURS"] = "1"
        ok, reason, _ = check_new_order_allowed_full(
            venue="coinbase", operation="strict_stale", log_decision=False
        )
        stale_ok = ok is False and "stale" in reason

        _write_joint(
            joint,
            {
                "joint_review_id": "jr_deg",
                "packet_id": "pkt_d",
                "empty": False,
                "live_mode_recommendation": "normal",
                "review_integrity_state": "degraded",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        os.environ.pop("GOVERNANCE_JOINT_STALE_HOURS", None)
        ok, reason, _ = check_new_order_allowed_full(
            venue="coinbase", operation="strict_degraded", log_decision=False
        )
        degraded_ok = ok is False and "integrity" in reason

        _write_joint(
            joint,
            {
                "joint_review_id": "jr_unk",
                "packet_id": "pkt_u",
                "empty": False,
                "live_mode_recommendation": "not_a_real_mode",
                "review_integrity_state": "full",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        ok, reason, _ = check_new_order_allowed_full(
            venue="coinbase", operation="strict_unknown_mode", log_decision=False
        )
        unknown_ok = ok is False and reason == "unknown_live_mode_fail_closed"

    finally:
        if saved_ezr is not None:
            os.environ["EZRAS_RUNTIME_ROOT"] = saved_ezr
        else:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)

    verification_passed = bool(missing_ok and stale_ok and degraded_ok and unknown_ok)
    doc: Dict[str, Any] = {
        "schema": "governance_strict_verified_v1",
        "enforcement_enabled": True,
        "fail_closed_active": True,
        "missing_joint_blocks": missing_ok,
        "stale_joint_blocks": stale_ok,
        "degraded_blocks": degraded_ok,
        "verification_passed": verification_passed,
        "runtime_root": str(runtime_root.resolve()),
        "note": (
            "Includes unknown/non-normal live_mode in verification_passed (must deny). "
            "Per-category booleans reflect observed deny under GOVERNANCE_ORDER_ENFORCEMENT=true."
        ),
    }
    out_path = proof_dir / "governance_strict_verified.json"
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return {"path": str(out_path), "document": doc}
