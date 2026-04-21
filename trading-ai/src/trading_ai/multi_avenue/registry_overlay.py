"""Optional registry overlay under ``data/control/registry_overlay.json`` — additive only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def registry_overlay_path(*, runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = root / "data" / "control" / "registry_overlay.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_registry_overlay(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    p = registry_overlay_path(runtime_root=runtime_root)
    if not p.is_file():
        return {"version": 1, "additional_avenues": [], "additional_gates": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"version": 1, "additional_avenues": [], "additional_gates": {}}
        raw.setdefault("version", 1)
        raw.setdefault("additional_avenues", [])
        raw.setdefault("additional_gates", {})
        if not isinstance(raw["additional_avenues"], list):
            raw["additional_avenues"] = []
        if not isinstance(raw["additional_gates"], dict):
            raw["additional_gates"] = {}
        return raw
    except (OSError, json.JSONDecodeError, TypeError):
        return {"version": 1, "additional_avenues": [], "additional_gates": {}}


def save_registry_overlay(payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> Path:
    p = registry_overlay_path(runtime_root=runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = dict(payload)
    out.setdefault("version", 1)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def append_additional_gate(avenue_id: str, gate_id: str, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Register an extra gate on an existing or overlay avenue (idempotent)."""
    aid = str(avenue_id).strip()
    gid = str(gate_id).strip()
    if not aid or not gid:
        raise ValueError("avenue_id and gate_id required")
    cur = load_registry_overlay(runtime_root=runtime_root)
    ag: Dict[str, Any] = dict(cur.get("additional_gates") or {})
    lst = list(ag.get(aid) or [])
    if gid not in lst:
        lst.append(gid)
    ag[aid] = lst
    cur["additional_gates"] = ag
    save_registry_overlay(cur, runtime_root=runtime_root)
    return cur


def append_additional_avenue(avenue: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Append one avenue record to overlay (idempotent: replaces same avenue_id)."""
    cur = load_registry_overlay(runtime_root=runtime_root)
    aid = str(avenue.get("avenue_id") or "").strip()
    if not aid:
        raise ValueError("avenue_id required")
    add: List[Dict[str, Any]] = list(cur.get("additional_avenues") or [])
    add = [a for a in add if str(a.get("avenue_id")) != aid]
    add.append(dict(avenue))
    cur["additional_avenues"] = add
    save_registry_overlay(cur, runtime_root=runtime_root)
    return cur
