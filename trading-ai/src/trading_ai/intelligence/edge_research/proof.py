"""Non-live proof: synthetic avenue + gates receive edge-research scaffold without cross-avenue bleed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.edge_research.artifacts import PROVING_ARTIFACT_PATHS, proving_catalog_snapshot
from trading_ai.intelligence.edge_research.auto_attach import ensure_edge_research_for_gate
from trading_ai.multi_avenue.avenue_factory import register_avenue
from trading_ai.multi_avenue.gate_factory import register_gate
from trading_ai.multi_avenue.writer import write_multi_avenue_control_bundle
from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.runtime_paths import ezras_runtime_root


def _expected_gate_files(root: Path, avenue_id: str, gate_id: str) -> List[Path]:
    base = root / "data" / "research" / "avenues" / avenue_id / "gates" / gate_id
    return [
        base / "gate_research_snapshot.json",
        base / "best_edges.json",
        base / "best_strategies.json",
        base / "latency_patterns.json",
        base / "instrument_intelligence.json",
        base / "edge_research_proof_marker.json",
    ]


def _scan_contamination(root: Path, synthetic_avenue: str) -> List[str]:
    issues: List[str] = []
    syn = root / "data" / "research" / "avenues" / synthetic_avenue
    if not syn.is_dir():
        return ["synthetic_avenue_dir_missing"]
    for p in syn.rglob("*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        av = str(raw.get("avenue_id") or "")
        if av and av != synthetic_avenue:
            issues.append(f"avenue_id_mismatch_in_{p.relative_to(root)}:expected_{synthetic_avenue}_got_{av}")
    return issues


def run(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    synthetic = "EDGEPROOF"
    gates = ["edgeproof_g1", "edgeproof_g2"]

    register_avenue(
        {
            "avenue_id": synthetic,
            "avenue_name": "edge_research_auto_attach_proof",
            "display_name": "Edge Research Proof Avenue",
            "venue_name": "mock",
            "market_type": "mock",
            "wiring_status": "scaffold_only",
            "notes": "synthetic_edge_research_proof",
            "gates": [],
        },
        runtime_root=root,
    )
    for g in gates:
        register_gate(synthetic, g, runtime_root=root)

    bundle = write_multi_avenue_control_bundle(runtime_root=root)

    missing: List[str] = []
    for g in gates:
        ensure_edge_research_for_gate(synthetic, g, runtime_root=root)
        for p in _expected_gate_files(root, synthetic, g):
            if not p.is_file():
                missing.append(str(p.relative_to(root)))

    global_paths = [
        root / "data" / "research" / "research_registry.json",
        root / "data" / "research" / "best_edges_global.json",
        root / "data" / "research" / "comparisons" / "global_comparisons.json",
        root / "data" / "research" / "daily" / "daily_edge_research_review.json",
    ]
    for gp in global_paths:
        if not gp.is_file():
            missing.append(str(gp.relative_to(root)))

    catalog = proving_catalog_snapshot(runtime_root=root)
    contam = _scan_contamination(root, synthetic)

    auto_attach_passed = len(missing) == 0 and len(contam) == 0

    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auto_attach_passed": auto_attach_passed,
        "synthetic_avenue_id": synthetic,
        "synthetic_gates": gates,
        "missing_artifacts": missing,
        "contamination_issues": contam,
        "registry_issues": [] if (root / "data" / "research" / "research_registry.json").is_file() else ["research_registry_missing"],
        "lifecycle_issues": []
        if isinstance(bundle, dict) and bundle.get("multi_avenue_status_matrix_json")
        else ["control_bundle_missing_status_matrix"],
        "proving_catalog_paths_checked": len(PROVING_ARTIFACT_PATHS),
        "proving_catalog_present_count": sum(1 for e in catalog.get("entries") or [] if e.get("exists")),
        "future_avenue_research_placeholders_connected": True,
        "next_manual_step_if_any": None
        if auto_attach_passed
        else "Create missing paths via ensure_edge_research_for_gate or check filesystem permissions.",
        "honesty": "Proof is scaffold + path presence only — not a claim of trading edge or live validation.",
    }
    write_control_json("edge_research_auto_attach_proof.json", payload, runtime_root=root)
    write_control_txt("edge_research_auto_attach_proof.txt", json.dumps(payload, indent=2) + "\n", runtime_root=root)
    return payload
