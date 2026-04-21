"""Final go/no-go decision artifact."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.control.system_execution_lock import load_system_execution_lock
from trading_ai.prelive._io import write_control_json
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def build_go_no_go(*, runtime_root: Path) -> Dict[str, Any]:
    lock = load_system_execution_lock(runtime_root=runtime_root)
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    mirror = ad.read_json("data/control/execution_mirror_results.json") or {}
    harness = ad.read_json("data/control/mock_execution_harness_results.json") or {}
    blockers: list[str] = []
    if not mirror.get("ok"):
        blockers.append("execution_mirror_not_ok_or_missing")
    if not harness.get("scenarios"):
        blockers.append("mock_harness_empty")
    if not lock.get("ready_for_live_execution"):
        blockers.append("system_lock_not_ready_for_live_execution")
    ready_val = len(blockers) == 0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready_for_validation_rerun": ready_val,
        "ready_for_micro_validation": ready_val,
        "ready_for_first_5_trades": ready_val and bool(lock.get("gate_a_enabled")),
        "gate_a_ready": bool(lock.get("gate_a_enabled")),
        "gate_b_ready": bool(lock.get("gate_b_enabled")),
        "blockers": blockers,
        "operator_note": "If any blocker is listed, do not enable live execution flags until resolved.",
    }


def run(*, runtime_root: Path) -> Dict[str, Any]:
    payload = build_go_no_go(runtime_root=runtime_root)
    write_control_json("go_no_go_decision.json", payload, runtime_root=runtime_root)
    return payload
