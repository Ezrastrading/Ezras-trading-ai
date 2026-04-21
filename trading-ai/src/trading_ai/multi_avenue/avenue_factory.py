"""Controlled registration for new avenues — overlay + scaffold + factory audit trail."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.multi_avenue.attachment import compute_auto_attach_layers
from trading_ai.multi_avenue.auto_scaffold import ensure_avenue_scaffold
from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.multi_avenue.lifecycle_hooks import on_avenue_registered
from trading_ai.multi_avenue.registry_overlay import append_additional_avenue
from trading_ai.runtime_paths import ezras_runtime_root


def register_avenue(
    avenue_record: Dict[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Persist avenue to overlay, ensure filesystem scaffold, log factory event.

    Does not duplicate core registry rows — additional avenues only (see :func:`append_additional_avenue`).
    """
    aid = str(avenue_record.get("avenue_id") or "").strip()
    if not aid:
        raise ValueError("avenue_id required")
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    append_additional_avenue(dict(avenue_record), runtime_root=root)
    attach = compute_auto_attach_layers(avenue_id=aid, gate_id=None)
    scaffold = ensure_avenue_scaffold(aid, runtime_root=root)
    hook = on_avenue_registered(aid, runtime_root=root)
    append_control_events(
        "factory_events_log.json",
        {
            "event": "avenue_factory_register",
            "phase": "created_scaffolded_registered",
            "avenue_id": aid,
            "scaffold": scaffold,
            "hook": hook,
            "auto_attached_modules": attach.get("auto_attach_layers"),
        },
        runtime_root=root,
    )
    return {
        "status": "ok",
        "avenue_id": aid,
        "scaffold": scaffold,
        "auto_attach": attach,
    }
