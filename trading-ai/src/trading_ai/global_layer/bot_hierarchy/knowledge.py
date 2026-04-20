"""Durable mastery indexes — merged conservatively; no automatic strategy truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.bot_hierarchy.paths import default_bot_hierarchy_root, ensure_hierarchy_dirs

_KNOWLEDGE_FILES = (
    "avenue_master_knowledge.json",
    "gate_mastery_index.json",
    "gate_lessons_index.json",
    "strategy_knowledge_index.json",
    "venue_mechanics_index.json",
)


def _merge_json_file(path: Path, patch: Dict[str, Any]) -> None:
    prev: Dict[str, Any] = {}
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    if not isinstance(prev, dict):
        prev = {}
    prev.update(patch)
    prev.setdefault("truth_version", "bot_hierarchy_knowledge_v1")
    path.write_text(json.dumps(prev, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_avenue_master_knowledge(patch: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = ensure_hierarchy_dirs(root) / "knowledge"
    p = r / "avenue_master_knowledge.json"
    _merge_json_file(p, patch)
    return p


def merge_gate_mastery_index(patch: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = ensure_hierarchy_dirs(root) / "knowledge"
    p = r / "gate_mastery_index.json"
    _merge_json_file(p, patch)
    return p


def merge_gate_lessons_index(patch: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = ensure_hierarchy_dirs(root) / "knowledge"
    p = r / "gate_lessons_index.json"
    _merge_json_file(p, patch)
    return p


def merge_strategy_knowledge_index(patch: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = ensure_hierarchy_dirs(root) / "knowledge"
    p = r / "strategy_knowledge_index.json"
    _merge_json_file(p, patch)
    return p


def merge_venue_mechanics_index(patch: Dict[str, Any], *, root: Optional[Path] = None) -> Path:
    r = ensure_hierarchy_dirs(root) / "knowledge"
    p = r / "venue_mechanics_index.json"
    _merge_json_file(p, patch)
    return p


def load_knowledge_snapshot(*, root: Optional[Path] = None) -> Dict[str, Any]:
    r = ensure_hierarchy_dirs(root) / "knowledge"
    out: Dict[str, Any] = {}
    for name in _KNOWLEDGE_FILES:
        p = r / name
        if p.is_file():
            try:
                out[name.replace(".json", "")] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                out[name.replace(".json", "")] = {"honesty": "unreadable"}
        else:
            out[name.replace(".json", "")] = {}
    return out
