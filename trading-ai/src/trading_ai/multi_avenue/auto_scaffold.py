"""Idempotent filesystem scaffolding for avenues and gates — creates only missing pieces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions
from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.multi_avenue.scoped_paths import avenue_control_dir, avenue_review_dir, gate_control_dir, gate_review_dir
from trading_ai.runtime_paths import ezras_runtime_root


def _edge_research_fns() -> tuple:
    """Lazy import — avoids import-order failures when writer loads this module mid-init."""
    try:
        from trading_ai.intelligence.edge_research.auto_attach import (
            ensure_edge_research_for_avenue,
            ensure_edge_research_for_gate,
        )

        return ensure_edge_research_for_avenue, ensure_edge_research_for_gate
    except Exception:  # pragma: no cover - optional during partial installs
        return None, None


def _safe_slug(s: str, max_len: int) -> str:
    return "".join(c for c in str(s) if c.isalnum() or c in ("_", "-"))[:max_len]


def _mkdir(p: Path) -> bool:
    if p.exists():
        return False
    p.mkdir(parents=True, exist_ok=True)
    return True


def _write_json_if_missing(path: Path, payload: Dict[str, Any]) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return True


def ensure_avenue_scaffold(avenue_id: str, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Ensure per-avenue namespace folders, containers, and control snapshots exist.

    Idempotent: never overwrites existing files.
    """
    aid = _safe_slug(avenue_id, 32)
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    created: List[str] = []

    ar = avenue_review_dir(aid, runtime_root=root)
    subdirs = (
        ar / "namespace",
        ar / "progression",
        ar / "ceo_session",
        ar / "research",
        ar / "ratio",
        ar / "reserve",
        ar / "artifacts",
        ar / "scanner_registry",
    )
    for d in subdirs:
        if _mkdir(d):
            created.append(str(d.relative_to(root)))

    if _write_json_if_missing(
        ar / "namespace" / "scope_manifest.json",
        {"artifact": "namespace_scope_manifest", "avenue_id": aid, "implicit_scope_forbidden": True},
    ):
        created.append(str((ar / "namespace" / "scope_manifest.json").relative_to(root)))

    ac = avenue_control_dir(aid, runtime_root=root)
    snap = {
        "artifact": "avenue_status_snapshot",
        "avenue_id": aid,
        "scaffold_complete": True,
        "note": "Updated by ensure_avenue_scaffold — safe to extend with live metrics.",
    }
    if _write_json_if_missing(ac / "avenue_status_snapshot.json", snap):
        created.append(str((ac / "avenue_status_snapshot.json").relative_to(root)))

    er_paths: List[str] = []
    ensure_edge_research_for_avenue, _ = _edge_research_fns()
    if ensure_edge_research_for_avenue:
        try:
            er = ensure_edge_research_for_avenue(aid, runtime_root=root)
            er_paths = list(er.get("created_paths") or [])
        except Exception:
            er_paths = []
    if er_paths:
        created.extend(er_paths)

    if created:
        append_control_events(
            "auto_scaffold_log.json",
            {"action": "ensure_avenue_scaffold", "avenue_id": aid, "created_paths": created},
            runtime_root=root,
        )
    return {"avenue_id": aid, "runtime_root": str(root), "created_paths": created}


def ensure_gate_scaffold(avenue_id: str, gate_id: str, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Ensure per-gate scanner, edge, ratio, research, and control snapshots exist.

    Calls :func:`ensure_avenue_scaffold` first so the avenue tree exists.
    """
    aid = _safe_slug(avenue_id, 32)
    gid = _safe_slug(gate_id, 48)
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    avenue_out = ensure_avenue_scaffold(aid, runtime_root=root)
    from_avenue: List[str] = list(avenue_out.get("created_paths") or [])
    gate_only: List[str] = []

    gr = gate_review_dir(aid, gid, runtime_root=root)
    gsub = (
        gr / "scanner",
        gr / "scanner_output",
        gr / "scanner_review",
        gr / "edge_registry",
        gr / "edge_summary",
        gr / "ratio_view",
        gr / "research",
    )
    for d in gsub:
        if _mkdir(d):
            gate_only.append(str(d.relative_to(root)))

    meta = {
        "artifact": "scanner_metadata",
        "avenue_id": aid,
        "gate_id": gid,
        "scanner_framework_ready": True,
        "active_scanners_present": False,
    }
    if _write_json_if_missing(gr / "scanner_metadata.json", meta):
        gate_only.append(str((gr / "scanner_metadata.json").relative_to(root)))

    elig = {
        "artifact": "ceo_session_eligibility",
        "avenue_id": aid,
        "gate_id": gid,
        "eligible_for_scoped_ceo": True,
        "llm_ceo_wired": False,
        "note": "Framework shell only until LLM routing is explicitly wired.",
    }
    if _write_json_if_missing(gr / "ceo_session_eligibility.json", elig):
        gate_only.append(str((gr / "ceo_session_eligibility.json").relative_to(root)))

    gc = gate_control_dir(aid, gid, runtime_root=root)
    gsnap = {
        "artifact": "gate_status_snapshot",
        "avenue_id": aid,
        "gate_id": gid,
        "scaffold_complete": True,
    }
    if _write_json_if_missing(gc / "gate_status_snapshot.json", gsnap):
        gate_only.append(str((gc / "gate_status_snapshot.json").relative_to(root)))

    er_gate: List[str] = []
    _, ensure_edge_research_for_gate = _edge_research_fns()
    if ensure_edge_research_for_gate:
        try:
            er = ensure_edge_research_for_gate(aid, gid, runtime_root=root)
            er_gate = list(er.get("created_paths") or [])
        except Exception:
            er_gate = []
    if er_gate:
        gate_only.extend(er_gate)

    if gate_only:
        append_control_events(
            "auto_scaffold_log.json",
            {
                "action": "ensure_gate_scaffold",
                "avenue_id": aid,
                "gate_id": gid,
                "created_paths_gate_only": gate_only,
            },
            runtime_root=root,
        )
    return {
        "avenue_id": aid,
        "gate_id": gid,
        "runtime_root": str(root),
        "created_paths": from_avenue + gate_only,
    }


def ensure_all_registered_scaffolds(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Ensure scaffolds for every avenue and gate in the merged registry."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    summary: Dict[str, Any] = {"avenues": [], "gates": []}
    for av in merged_avenue_definitions(runtime_root=root):
        aid = str(av["avenue_id"])
        summary["avenues"].append(ensure_avenue_scaffold(aid, runtime_root=root))
        for gid in av.get("gates") or []:
            summary["gates"].append(ensure_gate_scaffold(aid, str(gid), runtime_root=root))
    return summary
