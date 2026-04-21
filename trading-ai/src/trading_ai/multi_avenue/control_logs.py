"""Append-only JSON logs under ``data/control`` for audits and operational proof."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _control_dir(runtime_root: Optional[Path]) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = root / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p


def append_control_events(
    filename: str,
    event: Dict[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Path:
    """Append one event to ``{filename}`` as ``{ "version": 1, "events": [...] }``."""
    ctrl = _control_dir(runtime_root)
    p = ctrl / filename
    data: Dict[str, Any] = {"version": 1, "events": []}
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("events"), list):
                data = raw
                data.setdefault("version", 1)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    ev = dict(event)
    ev.setdefault("timestamp_utc", _iso())
    events: List[Dict[str, Any]] = list(data.get("events") or [])
    events.append(ev)
    data["events"] = events[-2000:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)
    return p
