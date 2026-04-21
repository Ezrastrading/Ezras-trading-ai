"""Rollup report after all prelive steps."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.prelive.go_no_go import build_go_no_go


def run(*, runtime_root: Path) -> Dict[str, Any]:
    gng = build_go_no_go(runtime_root=runtime_root)
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "proved_without_live_capital": [
            "failsafe_guard logic (local)",
            "ledger append shape",
            "mock harness scenarios",
            "friction lab scenario catalog",
            "sizing sandbox arithmetic vs venue mins",
            "avenue C scaffold registration",
        ],
        "staged_proven": ["gate_b_status_report_json", "operator_interpretation_audit_structure"],
        "live_only_remaining": [
            "true fill latency",
            "account-specific minima",
            "venue maintenance windows",
        ],
        "gate_a_prelive_locked": gng.get("gate_a_ready"),
        "gate_b_prelive_locked": gng.get("gate_b_ready"),
        "avenue_auto_attach": True,
        "operator_visibility": "live_execution_state.json + failsafe_status.json required during live",
        "go_no_go": gng,
        "honesty": "Locked flags refer to configuration readiness — not trading performance.",
    }
    write_control_json("prelive_lock_report.json", payload, runtime_root=runtime_root)
    write_control_txt("prelive_lock_report.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    return payload
