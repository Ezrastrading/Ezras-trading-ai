"""Audit artifact wording — mock vs live vs blocked."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def run(*, runtime_root: Path) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    artifacts = [
        "data/control/final_readiness.json",
        "data/control/live_validation_streak.json",
        "data/control/system_truth_final.json",
        "data/control/go_no_go_decision.json",
        "data/control/universal_ratio_policy_snapshot.json",
    ]
    rows: List[Dict[str, Any]] = []
    for rel in artifacts:
        p = ad.root() / rel
        present = p.is_file()
        rows.append(
            {
                "path": rel,
                "present": present,
                "interpretation_label": "advisory-only" if not present else "verify_labels_inside_file",
                "risk": "absent_file_may_be_confused_with_blocked_feature" if not present else "none_known",
            }
        )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": rows,
        "rules": [
            "mock_proven must never be labeled live_proven",
            "code_ready must not imply capital_deployed",
        ],
    }
    write_control_json("operator_interpretation_audit.json", payload, runtime_root=runtime_root)
    write_control_txt("operator_interpretation_audit.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    return payload
