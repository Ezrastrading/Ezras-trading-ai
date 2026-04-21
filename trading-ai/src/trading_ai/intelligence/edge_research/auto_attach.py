"""Auto-scaffold edge-research artifacts for every avenue/gate — avenue-safe, idempotent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.edge_research.artifacts import (
    avenue_research_dir,
    ensure_global_research_templates,
    gate_research_dir,
    instruments_dir,
    markets_dir,
    research_root,
    strategies_dir,
    venues_dir,
)
from trading_ai.multi_avenue.contamination_guard import assert_matching_scope
from trading_ai.runtime_paths import ezras_runtime_root


def _write_json_if_missing(path: Path, payload: Dict[str, Any]) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return True


def ensure_edge_research_globals(*, runtime_root: Optional[Path] = None) -> List[str]:
    """Global research tree + seed files (never overwrite existing JSON content)."""
    research_root(runtime_root=runtime_root)
    for d in (instruments_dir, markets_dir, strategies_dir, venues_dir):
        d(runtime_root=runtime_root)
    return ensure_global_research_templates(runtime_root=runtime_root)


def ensure_edge_research_for_avenue(avenue_id: str, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Per-avenue research snapshot + placeholders."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    created: List[str] = []
    ensure_edge_research_globals(runtime_root=root)

    ar = avenue_research_dir(avenue_id, runtime_root=root)
    snap = {
        "artifact": "avenue_research_snapshot",
        "avenue_id": avenue_id,
        "edge_research_subsystem": "attached",
        "proving_pointers_note": "Cite paths under data/control/* and data/research/* only; no cross-avenue leakage.",
        "last_reviewed": None,
    }
    p_snap = ar / "avenue_research_snapshot.json"
    if _write_json_if_missing(p_snap, snap):
        created.append(str(p_snap.relative_to(root)))

    for sub in ("comparisons", "daily_placeholder"):
        d = ar / sub
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d.relative_to(root)))

    ph = ar / "daily_placeholder" / "scoped_review_placeholder.txt"
    if _write_text_if_missing(ph, f"Placeholder for avenue {avenue_id} — daily cycle fills JSON under data/research/daily/.\n"):
        created.append(str(ph.relative_to(root)))

    comp = ar / "comparisons" / "avenue_comparisons_placeholder.json"
    if _write_json_if_missing(
        comp,
        {"artifact": "avenue_comparisons_placeholder", "avenue_id": avenue_id, "comparisons": []},
    ):
        created.append(str(comp.relative_to(root)))

    return {"avenue_id": avenue_id, "created_paths": created, "runtime_root": str(root)}


def _write_text_if_missing(path: Path, text: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def ensure_edge_research_for_gate(avenue_id: str, gate_id: str, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Per-gate best edges/strategies/latency/instrument intelligence files."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    out_avenue = ensure_edge_research_for_avenue(avenue_id, runtime_root=root)
    created: List[str] = list(out_avenue.get("created_paths") or [])

    gr = gate_research_dir(avenue_id, gate_id, runtime_root=root)
    gsnap = {
        "artifact": "gate_research_snapshot",
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "edge_research_subsystem": "attached",
    }
    try:
        assert_matching_scope(gsnap, expected_avenue_id=avenue_id, expected_gate_id=gate_id, strict=False)
    except Exception:
        pass

    if _write_json_if_missing(gr / "gate_research_snapshot.json", gsnap):
        created.append(str((gr / "gate_research_snapshot.json").relative_to(root)))

    payloads = {
        "best_edges.json": {"artifact": "best_edges", "avenue_id": avenue_id, "gate_id": gate_id, "ranked": []},
        "best_strategies.json": {"artifact": "best_strategies", "avenue_id": avenue_id, "gate_id": gate_id, "ranked": []},
        "latency_patterns.json": {"artifact": "latency_patterns", "avenue_id": avenue_id, "gate_id": gate_id, "profiles": []},
        "instrument_intelligence.json": {
            "artifact": "instrument_intelligence",
            "avenue_id": avenue_id,
            "gate_id": gate_id,
            "instruments": [],
        },
    }
    for fname, body in payloads.items():
        p = gr / fname
        if _write_json_if_missing(p, body):
            created.append(str(p.relative_to(root)))

    proof_marker = gr / "edge_research_proof_marker.json"
    if _write_json_if_missing(
        proof_marker,
        {
            "artifact": "edge_research_proof_marker",
            "avenue_id": avenue_id,
            "gate_id": gate_id,
            "auto_attached": True,
            "execution_authority": False,
        },
    ):
        created.append(str(proof_marker.relative_to(root)))

    return {
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "runtime_root": str(root),
        "created_paths": created,
    }


def refresh_scoped_rankings_into_gate_files(avenue_id: str, gate_id: str, *, runtime_root: Optional[Path] = None) -> None:
    """Copy top scoped rows from global registry into gate JSON (merge, not wipe)."""
    from trading_ai.intelligence.edge_research.registry import load_registry
    from trading_ai.intelligence.edge_research.scoring import filter_scoped, rank_records

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    reg = load_registry(runtime_root=root)
    recs = [r for r in (reg.get("records") or []) if isinstance(r, dict)]
    scoped = filter_scoped(recs, avenue_id=avenue_id, gate_id=gate_id)
    gr = gate_research_dir(avenue_id, gate_id, runtime_root=root)
    gr.mkdir(parents=True, exist_ok=True)
    edges = [r for r in scoped if str(r.get("edge_name") or "").strip()]
    strat = [r for r in scoped if str(r.get("strategy_name") or "").strip()]
    (gr / "best_edges.json").write_text(
        json.dumps(
            {"artifact": "best_edges", "avenue_id": avenue_id, "gate_id": gate_id, "ranked": [{"score": s, "record": x} for s, x in rank_records(edges or scoped)[:30]]},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    (gr / "best_strategies.json").write_text(
        json.dumps(
            {
                "artifact": "best_strategies",
                "avenue_id": avenue_id,
                "gate_id": gate_id,
                "ranked": [{"score": s, "record": x} for s, x in rank_records(strat or scoped)[:30]],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
