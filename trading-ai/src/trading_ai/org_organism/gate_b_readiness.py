"""Gate B structural readiness report — honest; does not mask global brakes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.org_organism.io_utils import read_json_dict, write_json_atomic
from trading_ai.org_organism.paths import gate_b_readiness_report_path
from trading_ai.reports.gate_b_control_truth import write_gate_b_truth_artifacts
from trading_ai.shark.coinbase_spot.avenue_a_operator_status import build_avenue_a_operator_status
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report
from trading_ai.shark.coinbase_spot.gate_b_tuning_resolver import resolve_gate_b_tuning_artifact
from trading_ai.shark.coinbase_spot.gate_b_config import load_gate_b_config_from_env


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_gate_b_readiness_report(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    truth_bundle = write_gate_b_truth_artifacts(runtime_root=root)
    gb_live = gate_b_live_status_report()
    av = build_avenue_a_operator_status(runtime_root=root)
    gb_cfg = load_gate_b_config_from_env()
    tuning = resolve_gate_b_tuning_artifact(deployable_quote_usd=None, measured_slippage_bps=None, baseline_config=gb_cfg)

    snap = read_json_dict(root / "data" / "control" / "gate_b_selection_snapshot.json")
    compact = read_json_dict(root / "data" / "reports" / "gate_b_operator_readiness_compact.json")

    proof = read_json_dict(root / "execution_proof" / "gate_b_live_execution_validation.json")
    live_micro_ok = bool(proof.get("FINAL_EXECUTION_PROVEN")) if proof else False

    blockers: List[str] = []
    if not live_micro_ok:
        blockers.append("missing_or_failed_gate_b_live_coinbase_round_trip_proof")
    if isinstance(compact, dict):
        dva = compact.get("deployable_vs_advisory") or {}
        if isinstance(dva, dict) and dva.get("actually_deployable_live_orders") is False:
            blockers.append("gate_b_not_deployable_per_operator_readiness_compact")
        for x in (compact.get("blockers") or [])[:12]:
            blockers.append(str(x))
    if isinstance(snap, dict):
        for k in ("gate_b_supervised_operator_blockers", "operator_blockers"):
            raw = snap.get(k)
            if isinstance(raw, list):
                blockers.extend([str(x) for x in raw[:8]])
    blockers = list(dict.fromkeys(blockers))

    struct_ready = len(blockers) == 0 and bool(gb_live.get("gate_b_ready_for_live"))

    experiment_path_if_not_ready = [
        "python -m trading_ai.deployment gate-b-selection-smoke",
        "python -m trading_ai.deployment gate-b-live-micro --quote-usd 10 --product-id BTC-USD",
        "python -m trading_ai.deployment gate-b-tick",
    ]
    supervised_path_if_ready = [
        "python -m trading_ai.deployment supervised-readiness-closer",
        "python -m trading_ai.deployment avenue-a-daemon-once --quote-usd 10",
    ]

    out: Dict[str, Any] = {
        "truth_version": "gate_b_readiness_report_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "gate_b_structurally_ready_for_validation": struct_ready,
        "explicit_tuning_artifact": tuning,
        "explicit_candidate_selection_snapshot": {
            "path": "data/control/gate_b_selection_snapshot.json",
            "present": snap is not None,
            "summary_excerpt": (snap.get("selection_summary") if isinstance(snap, dict) else None),
        },
        "data_quality_summary": {
            "honesty": "Liquidity/volume fields in snapshots may be partial — see gate_a_operator_status operator_honesty_notes.",
            "liquidity_provenance_reference": "docs/LIQUIDITY_STABILITY_PROVENANCE.md (repo) + avenue_a_operator_status liquidity/stability summaries",
        },
        "liquidity_stability_provenance": {
            "avenue_status_field": av.get("liquidity_flag_provenance_summary"),
            "gate_b_tuning_calibration": tuning.get("calibration_level"),
        },
        "readiness_status": gb_live,
        "exact_blockers": blockers,
        "experiment_path_if_not_ready": experiment_path_if_not_ready,
        "supervised_path_if_ready": supervised_path_if_ready,
        "relationship_to_avenue_a_capital_and_mission": {
            "capital_split_fractions": (av.get("capital") or {}).get("split_fractions"),
            "mission_layer_note": "Mission execution state under data/control/organism/ references Avenue A goals; Gate B consumes gate_b share only.",
        },
        "reporting_to_avenue_master": "Artifacts: data/control/avenue_a_operator_status.json + gate_b truth paths from write_gate_b_truth_artifacts",
        "gate_b_truth_paths_written": truth_bundle.get("paths"),
        "honesty_if_not_ready": None if struct_ready else "Gate B is not fully ready — blockers above are authoritative; do not mask with optimism.",
    }
    write_json_atomic(gate_b_readiness_report_path(root), out)
    return out
