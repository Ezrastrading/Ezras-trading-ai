"""
Experiment registry — research-only contracts. No experiment grants live authority.

Experiments are advisory records; promotion ladder and proof artifacts remain authoritative.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from trading_ai.org_organism.io_utils import append_jsonl, read_json_dict, write_json_atomic
from trading_ai.org_organism.paths import (
    experiment_registry_path,
    experiment_results_path,
    experiment_summary_by_avenue_path,
    experiment_summary_by_gate_path,
)

EXPERIMENT_TYPES = frozenset(
    {
        "replay",
        "simulation",
        "execution_variant",
        "fill_variant",
        "sizing_variant",
        "timing_variant",
        "entry_variant",
        "exit_variant",
        "candidate_gate_probe",
    }
)
STATUSES = frozenset({"draft", "queued", "running", "passed", "failed", "stopped", "superseded"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_experiment_registry(runtime_root: Path) -> Dict[str, Any]:
    p = experiment_registry_path(runtime_root)
    base = read_json_dict(p) or {"truth_version": "experiment_registry_v1", "experiments": {}}
    if "experiments" not in base or not isinstance(base["experiments"], dict):
        base["experiments"] = {}
    base.setdefault("truth_version", "experiment_registry_v1")
    base.setdefault("advisory_only", True)
    base.setdefault("no_live_authority_from_registry", True)
    return base


def validate_experiment_record(rec: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    et = str(rec.get("experiment_type") or "")
    if et not in EXPERIMENT_TYPES:
        errs.append(f"invalid_experiment_type:{et}")
    st = str(rec.get("status") or "")
    if st and st not in STATUSES:
        errs.append(f"invalid_status:{st}")
    for k in (
        "hypothesis",
        "expected_edge_shape",
        "expected_failure_mode",
        "exact_success_criteria",
        "exact_stop_criteria",
    ):
        if not str(rec.get(k) or "").strip():
            errs.append(f"missing_or_empty:{k}")
    return errs


def register_experiment(
    runtime_root: Path,
    *,
    avenue_id: str,
    gate_id: str,
    parent_strategy_id: str,
    experiment_type: str,
    hypothesis: str,
    expected_edge_shape: str,
    expected_failure_mode: str,
    exact_success_criteria: str,
    exact_stop_criteria: str,
    max_duration_hours: float = 168.0,
    max_sample_count: int = 500,
    status: str = "draft",
) -> Dict[str, Any]:
    reg = load_experiment_registry(runtime_root)
    eid = f"exp_{uuid.uuid4().hex[:12]}"
    row: Dict[str, Any] = {
        "experiment_id": eid,
        "avenue_id": str(avenue_id),
        "gate_id": str(gate_id),
        "parent_strategy_id": str(parent_strategy_id),
        "experiment_type": str(experiment_type),
        "hypothesis": str(hypothesis),
        "expected_edge_shape": str(expected_edge_shape),
        "expected_failure_mode": str(expected_failure_mode),
        "exact_success_criteria": str(exact_success_criteria),
        "exact_stop_criteria": str(exact_stop_criteria),
        "max_allowed_duration_hours": float(max_duration_hours),
        "max_allowed_sample_count": int(max_sample_count),
        "status": str(status),
        "evidence_summary": "",
        "recommended_next_action": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    ve = validate_experiment_record(row)
    if ve:
        return {"ok": False, "validation_errors": ve, "experiment": None}
    reg["experiments"][eid] = row
    reg["updated_at"] = _now_iso()
    write_json_atomic(experiment_registry_path(runtime_root), reg)
    return {"ok": True, "experiment_id": eid, "experiment": row}


def append_experiment_result(
    runtime_root: Path,
    *,
    experiment_id: str,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row = {
        "ts": _now_iso(),
        "ts_unix": time.time(),
        "experiment_id": str(experiment_id),
        "event": str(event),
        "payload": payload or {},
        "advisory_only": True,
    }
    append_jsonl(experiment_results_path(runtime_root), row)
    return row


def build_experiment_status_report(runtime_root: Path) -> Dict[str, Any]:
    """Registry + summaries for operator CLI."""
    summaries = recompute_summaries(runtime_root)
    reg = load_experiment_registry(runtime_root)
    return {
        "truth_version": "experiment_status_report_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "no_live_authority_from_experiments": True,
        "registry": reg,
        "summaries": summaries,
    }


def recompute_summaries(runtime_root: Path) -> Dict[str, Any]:
    reg = load_experiment_registry(runtime_root)
    exps: Sequence[Dict[str, Any]] = list(reg.get("experiments", {}).values())
    by_gate: Dict[str, List[str]] = defaultdict(list)
    by_avenue: Dict[str, List[str]] = defaultdict(list)
    for e in exps:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("experiment_id") or "")
        by_gate[str(e.get("gate_id") or "unknown")].append(eid)
        by_avenue[str(e.get("avenue_id") or "unknown")].append(eid)
    sg = {
        "truth_version": "experiment_summary_by_gate_v1",
        "generated_at": _now_iso(),
        "counts_by_gate": {k: len(v) for k, v in by_gate.items()},
        "experiment_ids_by_gate": dict(by_gate),
        "advisory_only": True,
    }
    sa = {
        "truth_version": "experiment_summary_by_avenue_v1",
        "generated_at": _now_iso(),
        "counts_by_avenue": {k: len(v) for k, v in by_avenue.items()},
        "experiment_ids_by_avenue": dict(by_avenue),
        "advisory_only": True,
    }
    write_json_atomic(experiment_summary_by_gate_path(runtime_root), sg)
    write_json_atomic(experiment_summary_by_avenue_path(runtime_root), sa)
    return {"by_gate": sg, "by_avenue": sa}
