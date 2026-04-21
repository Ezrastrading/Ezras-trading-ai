"""Paths and idempotent writers for the edge-research filesystem (under EZRAS_RUNTIME_ROOT)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _safe_seg(s: str, max_len: int) -> str:
    return "".join(c for c in str(s) if c.isalnum() or c in ("_", "-"))[:max_len] or "unknown"


def research_root(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "research"
    p.mkdir(parents=True, exist_ok=True)
    return p


def registry_dir(runtime_root: Optional[Path] = None) -> Path:
    p = research_root(runtime_root=runtime_root) / "registry"
    p.mkdir(parents=True, exist_ok=True)
    return p


def avenue_research_dir(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    aid = _safe_seg(avenue_id, 32)
    p = research_root(runtime_root=runtime_root) / "avenues" / aid
    p.mkdir(parents=True, exist_ok=True)
    return p


def gate_research_dir(avenue_id: str, gate_id: str, runtime_root: Optional[Path] = None) -> Path:
    gid = _safe_seg(gate_id, 48)
    p = avenue_research_dir(avenue_id, runtime_root=runtime_root) / "gates" / gid
    p.mkdir(parents=True, exist_ok=True)
    return p


def instruments_dir(runtime_root: Optional[Path] = None) -> Path:
    p = research_root(runtime_root=runtime_root) / "instruments"
    p.mkdir(parents=True, exist_ok=True)
    return p


def markets_dir(runtime_root: Optional[Path] = None) -> Path:
    p = research_root(runtime_root=runtime_root) / "markets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def strategies_dir(runtime_root: Optional[Path] = None) -> Path:
    p = research_root(runtime_root=runtime_root) / "strategies"
    p.mkdir(parents=True, exist_ok=True)
    return p


def venues_dir(runtime_root: Optional[Path] = None) -> Path:
    p = research_root(runtime_root=runtime_root) / "venues"
    p.mkdir(parents=True, exist_ok=True)
    return p


def comparisons_dir(runtime_root: Optional[Path] = None) -> Path:
    p = research_root(runtime_root=runtime_root) / "comparisons"
    p.mkdir(parents=True, exist_ok=True)
    return p


def daily_dir(runtime_root: Optional[Path] = None) -> Path:
    p = research_root(runtime_root=runtime_root) / "daily"
    p.mkdir(parents=True, exist_ok=True)
    return p


# Proving / test layer artifacts this subsystem may cite (paths relative to runtime root).
PROVING_ARTIFACT_PATHS: List[str] = [
    "data/control/prelive_reality_matrix.json",
    "data/control/mock_execution_harness_results.json",
    "data/control/execution_friction_lab.json",
    "data/control/sizing_calibration_report.json",
    "data/control/gate_b_staged_validation.json",
    "data/control/avenue_auto_attach_proof.json",
    "data/control/deployment_truth_audit.json",
    "data/control/operator_interpretation_audit.json",
    "data/control/runtime_invocation_audit.json",
    "data/control/universal_ratio_policy_snapshot.json",
    "data/control/final_gap_closure_audit.json",
    "data/control/honest_live_status_matrix.json",
    "data/control/validation_product_resolution_report.json",
    "data/control/quote_capital_truth.json",
    "data/control/deployable_capital_report.json",
    "data/control/route_selection_report.json",
    "data/control/portfolio_truth_snapshot.json",
    "data/control/edge_research_auto_attach_proof.json",
]


def proving_catalog_snapshot(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Which proving artifacts exist — does not assign live truth without artifact content."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    rows = []
    for rel in PROVING_ARTIFACT_PATHS:
        p = root / rel
        rows.append(
            {
                "path": rel,
                "exists": p.is_file(),
                "max_inferable_evidence_tier": _infer_tier_from_path(rel),
            }
        )
    return {
        "artifact": "proving_layer_catalog",
        "runtime_root": str(root),
        "entries": rows,
        "honesty_note": "Presence alone does not upgrade findings to live_supported; use lifecycle rules.",
    }


def _infer_tier_from_path(rel: str) -> str:
    r = rel.lower()
    if "mock_execution" in r:
        return "mock_supported"
    if "harness" in r or "friction_lab" in r or "sizing_calibration" in r:
        return "mock_supported"
    if "staged_validation" in r or "prelive" in r or "gap_closure" in r:
        return "staged_supported"
    if "honest_live_status" in r or "portfolio_truth" in r or "quote_capital" in r:
        return "staged_or_live_context"
    return "under_research"


def _write_json_if_missing(path: Path, payload: Dict[str, Any]) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return True


def _write_text_if_missing(path: Path, text: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def ensure_global_research_templates(*, runtime_root: Optional[Path] = None) -> List[str]:
    """Seed global research files once (never overwrites)."""
    root = research_root(runtime_root=runtime_root)
    created: List[str] = []

    reg_json = root / "research_registry.json"
    if _write_json_if_missing(
        reg_json,
        {
            "artifact": "research_registry",
            "version": 1,
            "records": [],
            "comparisons": [],
            "honesty": "Empty registry — populated by discovery and manual research entries; merge-only writes.",
        },
    ):
        created.append(str(reg_json))

    reg_txt = root / "research_registry.txt"
    if _write_text_if_missing(reg_txt, "research_registry.json — use JSON for machine merges; this file is a human pointer.\n"):
        created.append(str(reg_txt))

    seeds = [
        ("best_edges_global.json", {"artifact": "best_edges_global", "ranked": [], "scope": "global"}),
        ("best_strategies_global.json", {"artifact": "best_strategies_global", "ranked": [], "scope": "global"}),
        ("best_latency_patterns_global.json", {"artifact": "best_latency_patterns_global", "ranked": [], "scope": "global"}),
    ]
    for name, body in seeds:
        p = root / name
        if _write_json_if_missing(p, body):
            created.append(str(p))

    comp = comparisons_dir(runtime_root=runtime_root) / "global_comparisons.json"
    if _write_json_if_missing(
        comp,
        {"artifact": "global_comparisons", "comparisons": [], "generated_by": "edge_research.comparisons"},
    ):
        created.append(str(comp))

    dd = daily_dir(runtime_root=runtime_root)
    dj = dd / "daily_edge_research_review.json"
    if _write_json_if_missing(dj, {"artifact": "daily_edge_research_review", "sessions": []}):
        created.append(str(dj))
    dt = dd / "daily_edge_research_review.txt"
    if _write_text_if_missing(dt, "Daily edge research — see JSON for structure.\n"):
        created.append(str(dt))

    return created
