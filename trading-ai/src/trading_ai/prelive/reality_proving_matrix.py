"""Canonical pre-live reality matrix (honest proof levels)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.prelive._io import write_control_json, write_control_txt


def build_matrix() -> List[Dict[str, Any]]:
    return [
        {
            "gap_id": "A",
            "title": "validation-to-reality",
            "why_it_matters": "Paper validation may diverge from venue fills, fees, and latency.",
            "what_can_be_proven_without_live_capital": "Mock venue, friction lab, mirror script, staged harness.",
            "what_cannot_be_proven_without_live_capital": "True slippage distribution and account-specific limits.",
            "current_proof_level": "code_tested",
            "proof_artifacts": ["data/control/mock_execution_harness_results.json", "scripts/execution_mirror_test.py"],
            "blockers": ["live micro-validation not yet run"],
            "recommended_next_step": "Micro-validation with smallest notional on one product.",
        },
        {
            "gap_id": "B",
            "title": "execution friction",
            "why_it_matters": "Partial fills and timeouts can strand exposure.",
            "what_can_be_proven_without_live_capital": "Synthetic scenarios in execution_friction_lab.",
            "what_cannot_be_proven_without_live_capital": "Exchange-specific partial-fill semantics under load.",
            "current_proof_level": "mock_proven",
            "proof_artifacts": ["data/control/execution_friction_lab.json"],
            "blockers": [],
            "recommended_next_step": "Observe first live partial fill handling.",
        },
        {
            "gap_id": "E",
            "title": "Gate B pre-live execution",
            "why_it_matters": "Kalshi path differs from Coinbase NTE.",
            "what_can_be_proven_without_live_capital": "Staged scanner/exit logic without orders.",
            "what_cannot_be_proven_without_live_capital": "Kalshi fill and fee truth.",
            "current_proof_level": "staged_proven",
            "proof_artifacts": ["data/control/gate_b_staged_validation.json"],
            "blockers": ["GATE_B_LIVE_EXECUTION_ENABLED gated"],
            "recommended_next_step": "Enable Gate B only after explicit validation artifact.",
        },
    ]


def run(*, runtime_root: Path) -> Dict[str, Any]:
    rows = build_matrix()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matrix": rows,
        "honesty_note": "Proof levels are labels — mock_proven does not equal live_proven.",
    }
    write_control_json("prelive_reality_matrix.json", payload, runtime_root=runtime_root)
    lines = ["EZRAS PRE-LIVE REALITY MATRIX", "=" * 50]
    for r in rows:
        lines.append(json.dumps(r, indent=2))
        lines.append("-" * 30)
    write_control_txt("prelive_reality_matrix.txt", "\n".join(lines) + "\n", runtime_root=runtime_root)
    return payload
