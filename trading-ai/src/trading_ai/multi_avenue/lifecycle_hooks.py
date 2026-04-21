"""
Scanner and autonomy handoff hooks (non-live).

Wires scanner cycles to durable downstream hints consumed by research/comparisons/tests.
Does not submit venue orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def on_scanner_cycle_export(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    After a scanner proof step, emit a durable snapshot for downstream autonomy layers.

    Increments ``scan_seq`` and records last proof linkage for comparisons / learning intake.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    snap_p = root / "data" / "control" / "scanner_autonomy_snapshot.json"
    prev = _read_json(snap_p)
    seq = int(prev.get("scan_seq") or 0) + 1
    payload = {
        "truth_version": "scanner_autonomy_snapshot_v1",
        "generated_at": _iso(),
        "scan_seq": seq,
        "runtime_root": str(root),
        "operational_proof_step": "scanner_cycle",
        "honesty": "Scanner hook emits durable state only; venue connectivity is out of scope here.",
    }
    _write_json(snap_p, payload)
    append_control_events(
        "scanner_downstream_events.json",
        {"event": "scanner_autonomy_snapshot", "scan_seq": seq},
        runtime_root=root,
    )
    return payload
