"""
Universal execution validation proof — ``final_execution_proven`` is strict (full round-trip + writes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.universal_execution.execution_truth_contract import ExecutionTruthContract


def build_universal_execution_proof_payload(
    bundle: Dict[str, Any],
    contract: ExecutionTruthContract,
) -> Dict[str, Any]:
    stages = contract.to_dict()
    entry_ok = bool(bundle.get("entry_fill") and bundle["entry_fill"].get("confirmed") is not False)
    if not entry_ok:
        entry_ok = _stage_ok(stages, "STAGE_3_ENTRY_FILL_CONFIRMED")
    exit_ok = bool(bundle.get("exit_fill") and bundle["exit_fill"].get("confirmed") is not False)
    if not exit_ok:
        exit_ok = _stage_ok(stages, "STAGE_5_EXIT_FILL_CONFIRMED")

    pnl_ok = _stage_ok(stages, "STAGE_6_PNL_VERIFIED")
    local_ok = _stage_ok(stages, "STAGE_7_LOCAL_DATA_WRITTEN")
    gov_ok = _stage_ok(stages, "STAGE_9_GOVERNANCE_LOGGED")
    review_ok = _stage_ok(stages, "STAGE_10_REVIEW_ARTIFACTS_UPDATED")

    rec = bundle.get("normalized_trade_record") or {}
    net = rec.get("net_pnl")

    remote_diag = bundle.get("remote_write") or {}
    remote_required = bool(remote_diag.get("remote_required", True))
    remote_stage_ok = _stage_ok(stages, "STAGE_8_REMOTE_DATA_WRITTEN")
    remote_ok = bool(remote_stage_ok or (not remote_required))

    partial_flags: List[str] = []
    raw_pf = bundle.get("partial_failure_flags")
    if isinstance(raw_pf, list):
        partial_flags = [str(x) for x in raw_pf if x]
    if bundle.get("partial_failure"):
        partial_flags.append("partial_failure")

    final_proven = bool(
        entry_ok
        and exit_ok
        and pnl_ok
        and local_ok
        and remote_ok
        and gov_ok
        and review_ok
        and (net is not None)
        and (not partial_flags)
    )

    return {
        "truth_version": "universal_execution_proof_v1",
        "execution_success": final_proven,
        "final_execution_proven": final_proven,
        "entry_fill_confirmed": entry_ok,
        "exit_fill_confirmed": exit_ok,
        "pnl_verified": pnl_ok,
        "databank_written": local_ok,
        "remote_synced": remote_ok,
        "governance_logged": gov_ok,
        "review_packet_updated": review_ok,
        "scheduler_stable": bool((bundle.get("adapter_proof") or {}).get("scheduler_stable")),
        "ready_for_next_cycle": bool(final_proven),
        "avenue_id": bundle.get("avenue_id") or rec.get("avenue_id"),
        "gate_id": rec.get("gate_id"),
        "strategy_id": rec.get("strategy_id"),
        "proof_kind": "universal_execution_validation",
        "proof_axis": "round_trip_plus_persistence",
        "blocking_reason": None if final_proven else "incomplete_stages_or_missing_pnl",
        "partial_failure_codes": [] if final_proven else (partial_flags or ["incomplete_universal_proof"]),
        "realized_pnl": net,
        "buy_leg_diagnostics": bundle.get("entry_submit"),
        "sell_leg_diagnostics": bundle.get("exit_submit"),
        "entry_leg_diagnostics": bundle.get("entry_fill"),
        "exit_leg_diagnostics": bundle.get("exit_fill"),
        "local_write_diagnostics": bundle.get("local_write"),
        "remote_write_diagnostics": bundle.get("remote_write"),
        "governance_diagnostics": bundle.get("governance"),
        "adaptive_diagnostics": bundle.get("pretrade_diagnostics", {}).get("adaptive") if isinstance(bundle.get("pretrade_diagnostics"), dict) else None,
        "duplicate_guard_diagnostics": bundle.get("pretrade_diagnostics", {}).get("duplicate_guard") if isinstance(bundle.get("pretrade_diagnostics"), dict) else None,
        "execution_truth_contract": stages,
    }


def _stage_ok(stages: Dict[str, Any], name: str) -> bool:
    s = stages.get(name) or {}
    return bool(s.get("ok"))


def write_universal_execution_validation(
    payload: Dict[str, Any],
    *,
    runtime_root: Optional[Any] = None,
) -> Dict[str, Any]:
    """Persist universal proof next to other control artifacts."""
    from trading_ai.runtime_paths import ezras_runtime_root

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    path = ctrl / "universal_execution_validation.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return {"path_json": str(path), "written": True, "artifact_name": "universal_execution_validation"}


def write_universal_execution_validation_from_bundle(
    bundle: Dict[str, Any],
    contract: ExecutionTruthContract,
    *,
    runtime_root: Optional[Any] = None,
) -> Dict[str, Any]:
    proof = build_universal_execution_proof_payload(bundle, contract)
    meta = write_universal_execution_validation(proof, runtime_root=runtime_root)
    return {"proof": proof, **meta}
