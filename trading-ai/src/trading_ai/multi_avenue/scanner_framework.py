"""Scanner registry framework — honest status when modules missing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.multi_avenue.gate_registry import merged_gate_rows
from trading_ai.runtime_paths import ezras_runtime_root


def build_scanner_framework_index(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Per-gate scanner attachment points. Does not fabricate scanners for unwired gates.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    by_gate: List[Dict[str, Any]] = []
    for g in merged_gate_rows(runtime_root=root):
        mods = g.get("active_scanner_modules") or []
        by_gate.append(
            {
                "avenue_id": g["avenue_id"],
                "gate_id": g["gate_id"],
                "scanner_framework_ready": True,
                "active_scanner_modules": mods,
                "no_active_scanner_modules_yet": len(mods) == 0,
                "scanner_review_placeholder": f"data/review/avenues/{g['avenue_id']}/gates/{g['gate_id']}/scanner_review_placeholder.json",
            }
        )
    return {
        "scanner_framework_version": "v1",
        "gates": by_gate,
        "future_avenue_behavior": {
            "auto_register_scanner_slot": True,
            "honest_default": "no_active_scanner_module_until_imported",
        },
    }


def build_scanner_framework_status(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Per-gate status file for ``data/control/scanner_framework_status.json``."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    gates: List[Dict[str, Any]] = []
    for g in merged_gate_rows(runtime_root=root):
        mods = g.get("active_scanner_modules") or []
        gates.append(
            {
                "avenue_id": g["avenue_id"],
                "gate_id": g["gate_id"],
                "scanner_registry_present": True,
                "scanner_framework_ready": True,
                "active_scanners_present": len(mods) > 0,
                "scanner_outputs_attached": len(mods) > 0,
                "research_attach_ready": True,
                "ceo_session_attach_ready": bool(g.get("review_eligibility")),
            }
        )
    return {
        "artifact": "scanner_framework_status",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "gates": gates,
    }


def write_scanner_framework_status(*, runtime_root: Optional[Path] = None) -> str:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_scanner_framework_status(runtime_root=root)
    p = ctrl / "scanner_framework_status.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(p)
