"""Gate B staged proof — scanner/exit logic + artifacts + parity matrices (no live venue orders)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.prelive.gate_b_micro_validation_runner import run as gate_b_micro_validation_run
from trading_ai.prelive.gate_b_runtime_proof import run as gate_b_runtime_proof_run
from trading_ai.reports.final_control_bundle import write_final_rerun_operator_pack
from trading_ai.reports.gate_parity_reports import (
    write_final_system_lock_status,
    write_full_system_lock_audit,
    write_gate_a_gate_b_parity_matrix,
    write_gate_a_gate_b_runtime_parity,
)
from trading_ai.reports.live_enablement_truth import write_live_enablement_truth
from trading_ai.intelligence.edge_research.gate_b_snapshot import write_gate_b_active_research_snapshot
from trading_ai.shark.coinbase_spot.gate_b_artifact_pipeline import write_gate_b_operational_artifacts
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report


def _write_gate_b_execution_validation(*, runtime_root: Path, rep: Dict[str, Any]) -> Dict[str, Any]:
    mic = False
    try:
        from trading_ai.shark.coinbase_spot.gate_b_live_status import load_gate_b_validation_record

        vr = load_gate_b_validation_record()
        mic = bool(vr and str(vr.get("micro_validation_pass") or "").lower() in ("1", "true", "yes"))
    except Exception:
        mic = False
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation_kind": "gate_b_staged_non_live",
        "gate_b_live_status": rep,
        "artifacts_required_for_live": [
            "data/control/gate_b_validation.json with validated_at and micro_validation_pass",
            "GATE_B_LIVE_EXECUTION_ENABLED=true",
            "Coinbase / governance paths exercised for intended live venue",
        ],
        "micro_ready_staged": mic,
        "live_order_ready_for_coinbase_operator": bool(rep.get("gate_b_ready_for_live")),
        "micro_ready_live_operator": bool(rep.get("gate_b_ready_for_live")),
        "continuous_production_loop_ready": False,
        "continuous_loop_semantics": (
            "live_order_ready / gate_b_ready_for_live means venue micro-validation + env — not a continuous "
            "production runner. See data/control/gate_b_loop_truth.json and `python -m trading_ai.deployment gate-b-tick`."
        ),
        "blocking_reasons_if_not_live_ready": [] if rep.get("gate_b_ready_for_live") else [
            "operator_disabled_or_missing_micro_validation_or_policy_block",
        ],
    }
    write_control_json("gate_b_execution_validation.json", payload, runtime_root=runtime_root)
    write_control_txt("gate_b_execution_validation.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    write_control_json("gate_b_validation_report.json", payload, runtime_root=runtime_root)
    write_control_txt("gate_b_validation_report.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    return payload


def _write_contamination_audit(*, runtime_root: Path) -> None:
    ctrl = runtime_root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    p = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate_a_artifact_prefixes": ["gate_a", "nte", "coinbase", "validation_product"],
        "gate_b_artifact_prefixes": ["gate_b", "coinbase_spot", "GATE_B_"],
        "rules": [
            "Ledger lines must carry gate_id where possible",
            "Intelligence tickets carry gate_id; Gate B never overwrites Gate A files",
        ],
    }
    (ctrl / "cross_gate_contamination_audit.json").write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8")


def run(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    prev_rt = os.environ.get("EZRAS_RUNTIME_ROOT")
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    try:
        micro_proof = gate_b_micro_validation_run(runtime_root=root, write_ledger=True)
        rep = gate_b_live_status_report()
        write_gate_b_operational_artifacts(runtime_root=root)
        proof = gate_b_runtime_proof_run(runtime_root=root)
        write_gate_b_active_research_snapshot(
            runtime_root=root,
            engine_edge=(proof.get("entry_evaluation") or {}).get("edge"),
        )
        parity = write_gate_a_gate_b_runtime_parity(runtime_root=root)
        parity_matrix = write_gate_a_gate_b_parity_matrix(runtime_root=root)
        lock = write_full_system_lock_audit(runtime_root=root)
        final_lock = write_final_system_lock_status(runtime_root=root)
        write_live_enablement_truth(runtime_root=root)
        write_final_rerun_operator_pack(runtime_root=root)
        exec_val = _write_gate_b_execution_validation(runtime_root=root, rep=rep)
        _write_contamination_audit(runtime_root=root)

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "staged_proofs": {
                "scanner_cadence": "GateBConfig.scan_interval_sec",
                "candidate_ranking": "GateBMomentumEngine.evaluate_entry_candidates",
                "exit_logic": ["profit", "trailing_stop", "hard_stop", "max_hold", "sudden_drop"],
                "artifacts": [
                    "gate_b_scan_results.json",
                    "gate_b_ranked_candidates.json",
                    "gate_b_selection_decisions.json",
                    "gate_b_risk_snapshot.json",
                    "gate_b_runtime_proof.json",
                ],
            },
            "gate_b_live_status": rep,
            "gate_b_runtime_proof": proof,
            "parity": parity,
            "parity_matrix": parity_matrix,
            "lock_audit": lock,
            "final_system_lock": final_lock,
        "live_enablement_truth_written": True,
        "final_rerun_operator_pack_written": True,
            "gate_b_micro_validation_proof_summary": {
                "all_passed": micro_proof.get("all_passed"),
                "validation_kind": micro_proof.get("validation_kind"),
            },
            "execution_validation": exec_val,
            "contamination_check": "gate_a and gate_b prefixes separated in cross_gate_contamination_audit.json",
            "honesty": "Gate B Coinbase spot-row paths are asserted in-process; venue fills remain unproven until live micro with capital at risk.",
        }
        write_control_json("gate_b_staged_validation.json", payload, runtime_root=root)
        write_control_txt("gate_b_staged_validation.txt", json.dumps(payload, indent=2, default=str) + "\n", runtime_root=root)
        gb_txt = root / "data" / "control" / "gate_b_readiness_honest.txt"
        gb_txt.parent.mkdir(parents=True, exist_ok=True)
        rs = rep.get("readiness_state") or "unknown"
        gb_txt.write_text(
            f"Gate B readiness_state={rs}. "
            "Live-ready requires gate_b_validation.json micro_validation_pass plus operator enable; "
            "see gate_b_execution_validation.json.\n",
            encoding="utf-8",
        )
        return payload
    finally:
        if prev_rt is None:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)
        else:
            os.environ["EZRAS_RUNTIME_ROOT"] = prev_rt
