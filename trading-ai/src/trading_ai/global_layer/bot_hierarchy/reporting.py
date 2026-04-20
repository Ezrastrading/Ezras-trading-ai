"""Structured manager/worker reports — advisory by default; never substitute for proof artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.bot_hierarchy.paths import default_bot_hierarchy_root, ensure_hierarchy_dirs


def _append_jsonl(root: Path, name: str, row: Dict[str, Any]) -> Path:
    ensure_hierarchy_dirs(root)
    p = root / "reports" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return p


def append_bot_report(
    row: Dict[str, Any],
    *,
    root: Optional[Path] = None,
) -> Path:
    r = dict(row)
    r.setdefault("advisory_only", True)
    r.setdefault("is_runtime_proof", False)
    r.setdefault("promotion_affecting", False)
    return _append_jsonl(root or default_bot_hierarchy_root(), "bot_report.jsonl", r)


def append_avenue_master_report(row: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = dict(row)
    r.setdefault("advisory_only", True)
    r.setdefault("is_runtime_proof", False)
    return _append_jsonl(root or default_bot_hierarchy_root(), "avenue_master_reports.jsonl", r)


def append_gate_manager_report(row: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = dict(row)
    r.setdefault("advisory_only", True)
    r.setdefault("is_runtime_proof", False)
    return _append_jsonl(root or default_bot_hierarchy_root(), "gate_manager_reports.jsonl", r)


def append_worker_report(row: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = dict(row)
    r.setdefault("advisory_only", True)
    r.setdefault("is_runtime_proof", False)
    return _append_jsonl(root or default_bot_hierarchy_root(), "worker_reports.jsonl", r)


def append_gate_research_report(row: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = dict(row)
    r.setdefault("advisory_only", True)
    return _append_jsonl(root or default_bot_hierarchy_root(), "gate_research_reports.jsonl", r)


def emit_guidance_downstream(
    *,
    from_bot_id: str,
    to_bot_id: str,
    guidance: str,
    avenue_id: str,
    gate_id: Optional[str],
    root: Optional[Path] = None,
) -> Path:
    """Teaching/support message — does not mutate live permissions."""
    return append_bot_report(
        {
            "report_type": "guidance",
            "reporter_bot_id": from_bot_id,
            "parent_bot_id": from_bot_id,
            "target_bot_id": to_bot_id,
            "avenue_id": avenue_id,
            "gate_id": gate_id,
            "observation": "",
            "recommendation": guidance,
            "confidence": 0.0,
            "evidence_pointers": [],
            "advisory_only": True,
            "promotion_affecting": False,
            "next_action_requested": "none",
        },
        root=root,
    )
