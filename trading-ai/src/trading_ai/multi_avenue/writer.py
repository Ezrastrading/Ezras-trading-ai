"""Write multi-avenue registries, audits, scoped templates, and compatibility notes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Set

from trading_ai.multi_avenue.auto_scaffold import ensure_all_registered_scaffolds
from trading_ai.multi_avenue.avenue_registry import build_avenue_registry_snapshot, merged_avenue_definitions
from trading_ai.multi_avenue.ceo_scoped import build_scoped_ceo_session_bundle
from trading_ai.multi_avenue.gate_registry import build_gate_registry_snapshot, merged_gate_rows
from trading_ai.multi_avenue.honest_not_live import write_honest_not_live_matrix
from trading_ai.multi_avenue.namespace_model import SessionScope
from trading_ai.multi_avenue.progression_scoped import build_progression_payload
from trading_ai.multi_avenue.scanner_framework import build_scanner_framework_index, write_scanner_framework_status
from trading_ai.multi_avenue.scoped_paths import (
    avenue_review_dir,
    gate_control_dir,
    gate_review_dir,
    legacy_flat_control,
    system_control_dir,
)
from trading_ai.multi_avenue.status_matrix import write_status_matrix_files
from trading_ai.multi_avenue.system_rollup_engine import write_system_rollup_snapshot
from trading_ai.multi_avenue.universalization_audit import write_universalization_audit_files
from trading_ai.runtime_paths import ezras_runtime_root


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


def write_multi_avenue_control_bundle(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    """
    Writes control artifacts + scoped templates. Preserves legacy flat ``data/control/*`` files
    (does not move or delete them). Adds parallel scoped tree under ``data/control/avenues/`` etc.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    out: Dict[str, Any] = {"runtime_root": str(root)}

    ensure_all_registered_scaffolds(runtime_root=root)

    ctrl = legacy_flat_control(root)
    av_snap = build_avenue_registry_snapshot(runtime_root=root)
    g_snap = build_gate_registry_snapshot(runtime_root=root)
    out["avenue_registry_snapshot_json"] = _write_json(ctrl / "avenue_registry_snapshot.json", av_snap)
    out["gate_registry_snapshot_json"] = _write_json(ctrl / "gate_registry_snapshot.json", g_snap)

    # txt mirrors
    (ctrl / "avenue_registry_snapshot.txt").write_text(
        json.dumps(av_snap, indent=2, default=str)[:28000] + "\n", encoding="utf-8"
    )
    (ctrl / "gate_registry_snapshot.txt").write_text(
        json.dumps(g_snap, indent=2, default=str)[:28000] + "\n", encoding="utf-8"
    )

    out.update(write_universalization_audit_files(runtime_root=root))
    out.update(write_status_matrix_files(runtime_root=root))

    sysdir = system_control_dir(root)
    review = root / "data" / "review"
    review.mkdir(parents=True, exist_ok=True)
    prog_sys = build_progression_payload(scope_level="system")
    out["progression_system_json"] = _write_json(review / "progression_system.json", prog_sys)

    compat = {
        "note": "Legacy flat control files (ratio_policy_snapshot.json, etc.) remain authoritative for current runtime.",
        "scoped_additions": "data/control/avenues/{avenue_id}/... is additive — no deletion of flat files.",
    }
    out["compatibility_mirror_note_json"] = _write_json(sysdir / "legacy_flat_compatibility_note.json", compat)

    scanner_ix = build_scanner_framework_index(runtime_root=root)
    out["scanner_framework_index_json"] = _write_json(sysdir / "scanner_framework_index.json", scanner_ix)
    out["scanner_framework_status_json"] = write_scanner_framework_status(runtime_root=root)
    out["honest_not_live_matrix_json"] = write_honest_not_live_matrix(runtime_root=root)
    out["system_rollup_snapshot_json"] = write_system_rollup_snapshot(runtime_root=root)

    seen_avenues: Set[str] = set()
    for av in merged_avenue_definitions(runtime_root=root):
        aid = str(av["avenue_id"])
        if aid in seen_avenues:
            continue
        seen_avenues.add(aid)
        ad = avenue_review_dir(aid, runtime_root=root)
        p_av = build_progression_payload(scope_level="avenue", avenue_id=aid)
        out[f"progression_avenue_{aid}_json"] = _write_json(ad / "progression_avenue.json", p_av)
        ceo_a = build_scoped_ceo_session_bundle(
            session_scope=SessionScope.AVENUE.value,
            avenue_id=aid,
        )
        out[f"ceo_session_avenue_{aid}_json"] = _write_json(ad / "ceo_session_scoped.json", ceo_a)

    for g in merged_gate_rows(runtime_root=root):
        aid = str(g["avenue_id"])
        gid = str(g["gate_id"])
        gd = gate_review_dir(aid, gid, runtime_root=root)
        gctrl = gate_control_dir(aid, gid, runtime_root=root)
        p_g = build_progression_payload(scope_level="gate", avenue_id=aid, gate_id=gid)
        out[f"progression_gate_{aid}_{gid}_json"] = _write_json(gd / "progression_gate.json", p_g)
        ceo_g = build_scoped_ceo_session_bundle(
            session_scope=SessionScope.GATE.value,
            avenue_id=aid,
            gate_id=gid,
        )
        out[f"ceo_session_gate_{aid}_{gid}_json"] = _write_json(gd / "ceo_session_scoped.json", ceo_g)
        mods = g.get("active_scanner_modules") or []
        ph = {
            "scope_level": "gate",
            "avenue_id": aid,
            "gate_id": gid,
            "scanner_framework_ready": True,
            "no_active_scanner_module": len(mods) == 0,
            "review_framework_ready": True,
            "execution_not_present": not bool(g.get("execution_present")),
            "notes": "Placeholder — replace with scanner health JSON when available.",
        }
        _write_json(gd / "scanner_review_placeholder.json", ph)
        _write_json(gctrl / "namespace_scope_marker.json", {"avenue_id": aid, "gate_id": gid})

    ceo_sys = build_scoped_ceo_session_bundle(session_scope=SessionScope.SYSTEM_WIDE.value)
    out["ceo_session_system_json"] = _write_json(review / "ceo_session_system_pointer.json", ceo_sys)

    return out
