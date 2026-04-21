"""Merge-only research registry — never destructive overwrite of existing record content."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.edge_research.models import (
    ResearchComparisonRecord,
    ResearchRecordCore,
    parse_comparison_dict,
    parse_record_dict,
)
from trading_ai.intelligence.edge_research.artifacts import research_root


def registry_json_path(runtime_root: Optional[Path] = None) -> Path:
    return research_root(runtime_root=runtime_root) / "research_registry.json"


def load_registry(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    p = registry_json_path(runtime_root=runtime_root)
    if not p.is_file():
        return {"artifact": "research_registry", "version": 1, "records": [], "comparisons": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("records", [])
            raw.setdefault("comparisons", [])
            return raw
    except json.JSONDecodeError:
        pass
    return {"artifact": "research_registry", "version": 1, "records": [], "comparisons": []}


def save_registry(payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> None:
    p = registry_json_path(runtime_root=runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    txt = p.with_suffix(".txt")
    if not txt.exists():
        txt.write_text(
            "Machine registry: research_registry.json\n"
            "Merge-only updates; do not hand-edit unless you understand record_id stability.\n",
            encoding="utf-8",
        )


def merge_records(
    incoming: List[Dict[str, Any]],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Merge by record_id — newer updated_at wins when both exist."""
    base = load_registry(runtime_root=runtime_root)
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in base.get("records") or []:
        if isinstance(r, dict) and r.get("record_id"):
            by_id[str(r["record_id"])] = dict(r)
    for r in incoming:
        if not isinstance(r, dict) or not r.get("record_id"):
            continue
        rid = str(r["record_id"])
        if rid in by_id:
            old_u = by_id[rid].get("updated_at") or ""
            new_u = r.get("updated_at") or ""
            if new_u >= old_u:
                merged = {**by_id[rid], **r}
                by_id[rid] = merged
        else:
            by_id[rid] = dict(r)
    base["records"] = list(by_id.values())
    base["updated_merge_at"] = datetime.now(timezone.utc).isoformat()
    save_registry(base, runtime_root=runtime_root)
    return base


def merge_comparisons(
    incoming: List[Dict[str, Any]],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    base = load_registry(runtime_root=runtime_root)
    by_id: Dict[str, Dict[str, Any]] = {}
    for c in base.get("comparisons") or []:
        if isinstance(c, dict) and c.get("record_id"):
            by_id[str(c["record_id"])] = dict(c)
    for c in incoming:
        if not isinstance(c, dict) or not c.get("record_id"):
            continue
        rid = str(c["record_id"])
        if rid in by_id:
            old_u = by_id[rid].get("updated_at") or ""
            new_u = c.get("updated_at") or ""
            if new_u >= old_u:
                by_id[rid] = {**by_id[rid], **c}
        else:
            by_id[rid] = dict(c)
    base["comparisons"] = list(by_id.values())
    save_registry(base, runtime_root=runtime_root)
    return base


def records_as_models(*, runtime_root: Optional[Path] = None) -> List[ResearchRecordCore]:
    out: List[ResearchRecordCore] = []
    for r in load_registry(runtime_root=runtime_root).get("records") or []:
        if isinstance(r, dict):
            try:
                out.append(parse_record_dict(r))
            except Exception:
                continue
    return out


def comparisons_as_models(*, runtime_root: Optional[Path] = None) -> List[ResearchComparisonRecord]:
    out: List[ResearchComparisonRecord] = []
    for c in load_registry(runtime_root=runtime_root).get("comparisons") or []:
        if isinstance(c, dict):
            try:
                out.append(parse_comparison_dict(c))
            except Exception:
                continue
    return out
