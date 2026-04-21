"""
Map execution_proof/gate_b_live_execution_validation.json → universal_execution_loop_proof.json
only when Gate B micro proof fully satisfies the same booleans as FINAL_EXECUTION_PROVEN.

Does not duplicate trading logic — reads persisted proof only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.universal_execution.execution_truth_contract import ExecutionTruthStage
from trading_ai.universal_execution.rebuy_policy import TerminalHonestState
from trading_ai.universal_execution.universal_execution_loop_proof import (
    build_universal_execution_loop_proof_payload,
    write_universal_execution_loop_proof,
)
from trading_ai.runtime_proof.live_validation_terminal_failure import proof_contract_violation_messages


def _gate_b_proof_path(runtime_root: Path) -> Path:
    return runtime_root / "execution_proof" / "gate_b_live_execution_validation.json"


def _gate_a_proof_path(runtime_root: Path) -> Path:
    return runtime_root / "execution_proof" / "live_execution_validation.json"


def gate_b_file_proves_full_contract(g: Dict[str, Any]) -> Tuple[bool, str]:
    """Strict: same pipeline fields as live_execution_validation proof merge before write."""
    viol = proof_contract_violation_messages(g)
    if viol:
        return False, viol[0]
    return True, "ok"


def _synthetic_trade_result_from_gate_proof(g: Dict[str, Any], *, proof_source: str) -> Dict[str, Any]:
    """Build minimal result dict for build_universal_execution_loop_proof_payload."""
    stages_flat: Dict[str, Any] = {}
    pk = str(g.get("proof_kind") or "gate_b_live_micro_coinbase_round_trip")
    for st in ExecutionTruthStage:
        if st.value > 10:
            break
        stages_flat[st.name] = {
            "ok": True,
            "proof_source": proof_source,
            "proof_kind": pk,
        }
    trade_id = str(
        g.get("trade_id")
        or g.get("validation_trade_id")
        or g.get("validation_scope_duplicate_isolation_key")
        or "gate_b_micro"
    )
    return {
        "trade_id": trade_id,
        "execution_truth_contract": stages_flat,
        "bundle": {
            "universal_proof": {"final_execution_proven": True},
            "remote_write": {"remote_required": True},
            "trade_id": trade_id,
        },
        "final_execution_proven": True,
        "cycle_ok": True,
        "terminal_honest_state": TerminalHonestState.ROUND_TRIP_SUCCESS.value,
    }


def try_emit_universal_loop_proof_from_gate_b_file(
    *,
    runtime_root: Path,
    overwrite_if_unproven: bool = True,
) -> Dict[str, Any]:
    """
    If Gate B proof file proves full contract, write universal_execution_loop_proof.json via existing writer.

    If universal proof already has final_execution_proven true, skip.
    If universal proof exists but unproven and overwrite_if_unproven, replace only when gate proves success.
    """
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    proof_path = _gate_b_proof_path(root)
    if not proof_path.is_file():
        return {"emitted": False, "reason": "no_gate_b_live_execution_validation_json"}

    try:
        g = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"emitted": False, "reason": f"read_error:{exc}"}

    ok, why = gate_b_file_proves_full_contract(g)
    if not ok:
        viol = proof_contract_violation_messages(g)
        return {
            "emitted": False,
            "reason": "proof_contract_not_satisfied",
            "blocking_condition": why,
            "proof_fields_missing_or_false": viol,
            "gate_b_path": str(proof_path),
        }

    existing = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    if existing.get("final_execution_proven") is True:
        return {"emitted": False, "reason": "universal_proof_already_proven", "skipped": True}
    if existing and not overwrite_if_unproven:
        return {"emitted": False, "reason": "universal_exists_overwrite_disabled"}

    result = _synthetic_trade_result_from_gate_proof(g, proof_source=str(proof_path))
    payload = build_universal_execution_loop_proof_payload(result)
    meta = write_universal_execution_loop_proof(payload, runtime_root=root)
    try:
        from trading_ai.first_20.integration import on_universal_loop_proof_written

        on_universal_loop_proof_written(payload, runtime_root=root)
    except Exception:
        pass
    # Do not call write_live_switch_closure_bundle here — avoid re-entrant closure storms when invoked from bundle.
    return {"emitted": True, "reason": "mapped_gate_b_proof_to_universal", **meta, "loop_proof": payload}


def try_emit_universal_loop_proof_from_gate_a_file(
    *,
    runtime_root: Path,
    overwrite_if_unproven: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Map ``execution_proof/live_execution_validation.json`` (Gate A) → universal loop proof when strict
    booleans match the live validation pipeline (same contract as Gate B file).
    """
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    proof_path = _gate_a_proof_path(root)
    if not proof_path.is_file():
        return {"emitted": False, "reason": "no_live_execution_validation_json"}

    try:
        g = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"emitted": False, "reason": f"read_error:{exc}"}

    ok, why = gate_b_file_proves_full_contract(g)
    if not ok:
        viol = proof_contract_violation_messages(g)
        return {
            "emitted": False,
            "reason": "proof_contract_not_satisfied",
            "blocking_condition": why,
            "proof_fields_missing_or_false": viol,
            "gate_a_path": str(proof_path),
        }

    existing = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    if existing.get("final_execution_proven") is True and not force_refresh:
        return {"emitted": False, "reason": "universal_proof_already_proven", "skipped": True}
    if existing and not overwrite_if_unproven and not force_refresh:
        return {"emitted": False, "reason": "universal_exists_overwrite_disabled"}

    result = _synthetic_trade_result_from_gate_proof(
        g,
        proof_source=str(proof_path),
    )
    result["bundle"] = dict(result.get("bundle") or {})
    result["bundle"]["universal_proof"] = dict(result["bundle"].get("universal_proof") or {})
    result["bundle"]["universal_proof"]["proof_axis"] = "gate_a_live_execution_validation"
    payload = build_universal_execution_loop_proof_payload(result)
    meta = write_universal_execution_loop_proof(payload, runtime_root=root)
    try:
        from trading_ai.first_20.integration import on_universal_loop_proof_written

        on_universal_loop_proof_written(payload, runtime_root=root)
    except Exception:
        pass
    return {"emitted": True, "reason": "mapped_gate_a_proof_to_universal", **meta, "loop_proof": payload}
