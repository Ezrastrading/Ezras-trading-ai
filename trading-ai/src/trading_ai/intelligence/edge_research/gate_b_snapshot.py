"""Gate B / gainers research snapshot — honest, non-live, scoped to avenue A gate B."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def write_gate_b_active_research_snapshot(
    *,
    runtime_root: Optional[Path] = None,
    engine_edge: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "avenue_id": "A",
        "gate_id": "gate_b",
        "strategy_family": "gainer_breakout",
        "best_edges_gate_b": [
            "Continuation after volume-confirmed impulse (see GateBMomentumEngine).",
            "Tight spread + deep book near impulse (liquidity_gate).",
        ],
        "worst_traps_gate_b": [
            "Fake breakout without volume surge.",
            "Late entry after exhaustion_risk high.",
            "Liquidity collapse — monitor spread_bps.",
        ],
        "what_to_test_next": [
            "Kalshi ticker mapping vs spot row schema under live micro-validation.",
            "Partial fill + reject paths with real API.",
        ],
        "honesty": "Research scaffolding — not external market truth; validate with venue data.",
        "engine_edge_report": engine_edge or {},
    }
    p = ctrl / "gate_b_active_research_snapshot.json"
    p.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    (ctrl / "gate_b_active_research_snapshot.txt").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
