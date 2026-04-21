"""Truthful subsystem status — what is live vs scaffold vs advisory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def build_honest_not_live_matrix(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Each subsystem: one of
    live_and_enforced | runtime_invoked | validation_only | advisory_only |
    scaffold_only | not_implemented | intentionally_disabled
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    return {
        "artifact": "honest_not_live_matrix",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "subsystems": {
            "multi_avenue_registry_and_scaffold": "runtime_invoked",
            "lifecycle_hooks": "runtime_invoked",
            "scope_guards": "validation_only",
            "cross_avenue_rollup_engine": "validation_only",
            "multi_leg_execution": "not_implemented",
            "scanner_logic_unwired_gates": "scaffold_only",
            "venue_execution_future_avenues": "scaffold_only",
            "llm_ceo_sessions_scoped": "advisory_only",
            "gate_a_coinbase_nte": "live_and_enforced",
            "gate_b_kalshi": "live_and_enforced",
            "avenue_c_tastytrade": "scaffold_only",
        },
        "notes": [
            "Statuses are honest labels — not marketing.",
            "Re-run bundle + readiness hooks to refresh after wiring changes.",
        ],
    }


def write_honest_not_live_matrix(*, runtime_root: Optional[Path] = None) -> str:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    p = ctrl / "honest_not_live_matrix.json"
    p.write_text(json.dumps(build_honest_not_live_matrix(runtime_root=root), indent=2, default=str), encoding="utf-8")
    return str(p)
