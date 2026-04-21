"""
Canonical read model for bots — **read-only** mirror of runtime artifacts.

Does not replace ``LocalStorageAdapter`` truth; aggregates pointers + summaries for governance layer.
Live execution must continue to use existing Gate A / Gate B proof paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def build_shared_truth(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ctrl = root / "data" / "control"
    proof = root / "execution_proof" / "live_execution_validation.json"

    def _read(rel: str) -> Dict[str, Any]:
        p = root / rel
        if not p.is_file():
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    gate_a = _read("execution_proof/live_execution_validation.json")
    om = _read("data/control/operating_mode_state.json")
    rr_fail = _read("data/control/runtime_runner_last_failure.json")
    rr_cycle = _read("data/control/runtime_runner_last_cycle.json")
    halt = _read("data/control/gate_b_global_halt_truth.json")

    return {
        "truth_version": "shared_truth_read_model_v1",
        "runtime_root": str(root),
        "active_venue_state": {"operating_mode": om},
        "active_gate_state": {
            "gate_a_proof_success": bool(gate_a.get("execution_success")),
            "gate_a_final_proven": bool(gate_a.get("FINAL_EXECUTION_PROVEN")),
            "runtime_root_in_proof": gate_a.get("runtime_root"),
        },
        "open_positions": {},  # populated when position store is unified; keep explicit empty vs inventing
        "latest_fills": [],
        "latest_pnl": gate_a.get("realized_pnl"),
        "risk_mode": halt.get("global_halt_primary_classification"),
        "operating_mode": om.get("mode"),
        "last_execution_result": {
            "trade_id": gate_a.get("trade_id"),
            "failure_reason": gate_a.get("failure_reason"),
        },
        "last_known_health": {
            "runtime_runner_last_cycle_present": rr_cycle != {},
            "runtime_runner_last_failure_present": rr_fail != {},
        },
        "trade_event_refs": {
            "live_execution_validation": str(proof) if proof.is_file() else None,
            "control_dir": str(ctrl) if ctrl.is_dir() else None,
        },
        "honesty": (
            "Read-only mirror. Open positions / fills must be wired to venue-specific stores in a later phase "
            "without duplicating execution truth."
        ),
    }
