"""Safe aggregation — only structured, scope-labeled totals; never raw cross-avenue blobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions
from trading_ai.multi_avenue.gate_registry import merged_gate_rows
from trading_ai.runtime_paths import ezras_runtime_root


def build_system_rollup_snapshot(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    avenues = merged_avenue_definitions(runtime_root=root)
    gates = merged_gate_rows(runtime_root=root)
    return {
        "artifact": "system_rollup_snapshot",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "rule": "Never merge unlabeled trade rows — use per-scope keys only.",
        "system_wide": {
            "pnl_usd": None,
            "progression": None,
            "capital_summary": None,
            "note": "Populate only from explicitly aggregated scoped inputs.",
        },
        "by_avenue": {
            str(a["avenue_id"]): {
                "avenue_id": str(a["avenue_id"]),
                "pnl_usd": None,
                "progression": None,
                "ratio_summary": None,
                "capital_summary": None,
            }
            for a in avenues
        },
        "by_gate": [
            {
                "avenue_id": g["avenue_id"],
                "gate_id": g["gate_id"],
                "pnl_usd": None,
                "edge_performance": None,
                "ratio_summary": None,
            }
            for g in gates
        ],
    }


def write_system_rollup_snapshot(*, runtime_root: Optional[Path] = None) -> str:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    p = ctrl / "system_rollup_snapshot.json"
    p.write_text(json.dumps(build_system_rollup_snapshot(runtime_root=root), indent=2, default=str), encoding="utf-8")
    return str(p)
