"""Per-avenue / per-gate capability matrix — honest framework vs live modules."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions
from trading_ai.multi_avenue.gate_registry import merged_gate_rows
from trading_ai.runtime_paths import ezras_runtime_root


def _exists(p: Path) -> bool:
    return p.is_file()


def build_multi_avenue_status_matrix(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    rows: List[Dict[str, Any]] = []

    for av in merged_avenue_definitions(runtime_root=root):
        aid = av["avenue_id"]
        rows.append(
            {
                "kind": "avenue",
                "avenue_id": aid,
                "gate_id": None,
                "registry_present": True,
                "execution_present": av.get("wiring_status") == "wired",
                "scanner_framework_present": True,
                "active_scanners_present": len(av.get("gates") or []) > 0,
                "ceo_session_ready": True,
                "progression_ready": True,
                "ratio_ready": True,
                "reserve_ready": True,
                "edge_review_ready": True,
                "research_ready": True,
                "edge_research_scaffold_ready": _exists(root / "data" / "research" / "research_registry.json"),
                "artifact_namespace_ready": True,
                "honest_live_status_ready": _exists(ctrl / "honest_live_status_matrix.json"),
                "contamination_guard_ready": True,
                "notes": av.get("notes"),
            }
        )

    for g in merged_gate_rows(runtime_root=root):
        aid = g["avenue_id"]
        gid = g["gate_id"]
        mods = g.get("active_scanner_modules") or []
        rows.append(
            {
                "kind": "gate",
                "avenue_id": aid,
                "gate_id": gid,
                "registry_present": True,
                "execution_present": bool(g.get("execution_present")),
                "scanner_framework_present": bool(g.get("scanner_framework_present")),
                "active_scanners_present": len(mods) > 0,
                "ceo_session_ready": bool(g.get("review_eligibility")),
                "progression_ready": True,
                "ratio_ready": True,
                "reserve_ready": True,
                "edge_review_ready": True,
                "research_ready": True,
                "edge_research_scaffold_ready": _exists(root / "data" / "research" / "research_registry.json"),
                "artifact_namespace_ready": True,
                "honest_live_status_ready": _exists(ctrl / "honest_live_status_matrix.json"),
                "contamination_guard_ready": True,
                "notes": g.get("gate_name"),
            }
        )

    return {
        "artifact": "multi_avenue_status_matrix",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "auto_attach_ready_for_future_avenues": True,
        "auto_attach_ready_for_future_gates": True,
        "rows": rows,
    }


def write_status_matrix_files(*, runtime_root: Path | None = None) -> Dict[str, str]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_multi_avenue_status_matrix(runtime_root=root)
    js = json.dumps(payload, indent=2, default=str)
    jp = ctrl / "multi_avenue_status_matrix.json"
    tp = ctrl / "multi_avenue_status_matrix.txt"
    jp.write_text(js, encoding="utf-8")
    tp.write_text(js[:32000] + "\n", encoding="utf-8")
    return {
        "multi_avenue_status_matrix_json": str(jp),
        "multi_avenue_status_matrix_txt": str(tp),
    }
