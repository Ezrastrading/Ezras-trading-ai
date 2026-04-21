"""Append operational proof events for tests and smoke runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.runtime_paths import ezras_runtime_root


def record_operational_proof(
    step: str,
    *,
    detail: Optional[Dict[str, Any]] = None,
    runtime_root: Optional[Path] = None,
) -> None:
    payload: Dict[str, Any] = {"step": step, "runtime_root": str(Path(runtime_root or ezras_runtime_root()).resolve())}
    if detail:
        payload["detail"] = detail
    append_control_events("operational_proof_log.json", payload, runtime_root=runtime_root)
