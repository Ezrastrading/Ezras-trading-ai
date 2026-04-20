"""Operator-facing daemon status — no orders."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from trading_ai.orchestration.armed_but_off_authority import classify_final_daemon_go_live
from trading_ai.orchestration.autonomous_daemon_live_contract import (
    env_autonomous_daemon_live_enabled,
    read_autonomous_daemon_live_enable,
)
from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def build_daemon_operator_status(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    cls, note = classify_final_daemon_go_live(runtime_root=root)
    st = avenue_a_daemon_status(runtime_root=root)
    return {
        "runtime_root": str(root),
        "avenue_a_daemon": st,
        "EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED": env_autonomous_daemon_live_enabled(),
        "autonomous_daemon_live_enable_artifact": read_autonomous_daemon_live_enable(runtime_root=root),
        "final_daemon_go_live_authority": ad.read_json("data/control/final_daemon_go_live_authority.json") or {},
        "live_matrix": ad.read_json("data/control/universal_avenue_gate_live_matrix.json") or {},
        "autonomous_daemon_final_truth": ad.read_json("data/control/autonomous_daemon_final_truth.json") or {},
        "classification_computed": cls,
        "classification_note": note,
        "honesty": "Real order submission requires dual gate + policy; status is snapshot only.",
    }
