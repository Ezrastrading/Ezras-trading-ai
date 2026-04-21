"""Controlled registration for new gates — overlay + scaffold + factory audit trail."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.multi_avenue.attachment import compute_auto_attach_layers
from trading_ai.multi_avenue.auto_scaffold import ensure_gate_scaffold
from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.multi_avenue.lifecycle_hooks import on_gate_registered
from trading_ai.multi_avenue.registry_overlay import append_additional_gate
from trading_ai.runtime_paths import ezras_runtime_root


def register_gate(
    avenue_id: str,
    gate_id: str,
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add gate to overlay (when avenue exists in merged registry), scaffold, log."""
    aid = str(avenue_id).strip()
    gid = str(gate_id).strip()
    if not aid or not gid:
        raise ValueError("avenue_id and gate_id required")
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    append_additional_gate(aid, gid, runtime_root=root)
    attach = compute_auto_attach_layers(avenue_id=aid, gate_id=gid)
    scaffold = ensure_gate_scaffold(aid, gid, runtime_root=root)
    hook = on_gate_registered(aid, gid, runtime_root=root)
    append_control_events(
        "factory_events_log.json",
        {
            "event": "gate_factory_register",
            "phase": "created_scaffolded_registered",
            "avenue_id": aid,
            "gate_id": gid,
            "scaffold": scaffold,
            "hook": hook,
            "auto_attached_modules": attach.get("auto_attach_layers"),
        },
        runtime_root=root,
    )
    return {
        "status": "ok",
        "avenue_id": aid,
        "gate_id": gid,
        "scaffold": scaffold,
        "auto_attach": attach,
    }
